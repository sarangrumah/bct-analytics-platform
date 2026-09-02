"""Ground truth: what is actually running, and what this suite is forbidden to touch.

These assertions exist because every other test in the suite reads as a claim about *this* stack,
and a claim about a stack is worthless without saying which one. The last test in the file is the
one that matters most operationally: it proves the suite has not disturbed the three unrelated live
Docker stacks that share this host.
"""

from __future__ import annotations

import pytest

from conftest import run
from helpers import db

pytestmark = [pytest.mark.live]

# The four that predate the product split, plus the three that were promoted
# from `docker run` scripts into compose on 2026-09-01. Those three had working
# code, a Dockerfile and a reserved port, and appeared in no compose file at
# all -- so a "full" stack could report success with the login gateway, the
# metric API and the CDC loader all down, and the portal rendering nothing.
# They belong here for the same reason the first four do.
#
# cdc is NOT in this list. It only runs once a publication exists, which is
# `make cdc-start`, so requiring it here would fail a correct stack that has
# simply not been given a tenant to follow yet. test_08_freshness covers it.
REQUIRED = [
    "odoo19-bct-odoo",
    "odoo19-bct-postgres",
    "odoo19-bct-redis",
    "odoo19-bct-warehouse-db",
    "odoo19-bct-login-gateway",
    "odoo19-bct-semantic-api",
    "odoo19-bct-insight-portal",
]


def test_project_containers_are_running(evidence):
    out = run(["docker", "ps", "--filter", "name=odoo19-bct", "--format", "{{.Names}}\t{{.Status}}"])
    evidence.add("docker ps --filter name=odoo19-bct", out.stdout)
    names = {line.split("\t")[0] for line in out.stdout.splitlines() if line.strip()}
    missing = [c for c in REQUIRED if c not in names]
    assert not missing, "not running: %r" % (missing,)


def test_foreign_stacks_are_untouched(evidence):
    """The three other live stacks on this host must still be up after the suite has run.

    `make down`, `docker system prune` and an unscoped `docker compose down` would each take them
    with it, and their data is not recoverable from this repository. This test is the tripwire.
    """
    # Filtering happens in Python, not in a shell pipeline: `shell=True` on this host runs cmd.exe,
    # where `grep` does not exist and the pipeline silently produces an empty string -- which this
    # assertion would then read as "the other stacks are gone".
    out = run(["docker", "ps", "--format", "{{.Names}}"])
    foreign = [
        name for name in out.stdout.splitlines()
        if name.startswith(("odoo19-platform", "odoo19-analytics")) or "smart-warga" in name
    ]
    evidence.add(
        "docker ps, filtered to odoo19-platform / odoo19-analytics / smart-warga",
        "\n".join(foreign) or "(none)",
    )
    assert foreign, (
        "no odoo19-platform / odoo19-analytics / smart-warga container is running. Either they "
        "were never up, or something in this repository stopped them. Investigate before "
        "continuing -- this is the failure the project-scoping rule exists to prevent."
    )


def test_oltp_is_configured_for_logical_decoding(oltp_up, evidence):
    """ADR 0001 depends on two settings that are set at first boot and never changed live."""
    target = db.oltp_odoo()
    grid = db.grid(
        target,
        "SELECT name, setting, unit FROM pg_settings "
        "WHERE name IN ('wal_level','max_slot_wal_keep_size','max_replication_slots',"
        "'max_wal_senders') ORDER BY name;",
    )
    evidence.add("pg_settings on the OLTP database", grid)
    settings = dict((r[0], r[1]) for r in db.query(
        target,
        "SELECT name, setting FROM pg_settings WHERE name IN "
        "('wal_level','max_slot_wal_keep_size');",
    ))
    assert settings["wal_level"] == "logical", settings
    # max_slot_wal_keep_size is reported in MB. ADR 0001 fixes the cap at 2 GiB and makes the
    # alerting thresholds relative to it, so a changed cap silently invalidates those thresholds.
    assert settings["max_slot_wal_keep_size"] == "2048", (
        "max_slot_wal_keep_size is %s MB, not the 2048 MB (2 GiB) ADR 0001 fixed. The Prometheus "
        "warn/critical thresholds are 25%% and 50%% of that number and are now wrong."
        % settings["max_slot_wal_keep_size"]
    )


def test_warehouse_reader_is_read_only_by_construction(oltp_up, evidence):
    """Contract 04 §2 / ADR 0001: there is no write path from the warehouse into Odoo.

    Not by policy -- because the role cannot. Each denial is captured verbatim.
    """
    reader = db.oltp_reader()
    identity = db.role_identity(reader)
    evidence.add("identity", identity.grid)
    assert not identity.superuser, "warehouse_reader is a SUPERUSER; every denial below is vacuous"

    rows = db.query(
        reader,
        "SELECT rolname, rolsuper, rolreplication, rolcreatedb, rolcreaterole "
        "FROM pg_roles WHERE rolname = current_user;",
    )
    evidence.add(
        "attributes",
        db.grid(
            reader,
            "SELECT rolname, rolsuper, rolreplication, rolcreatedb, rolcreaterole "
            "FROM pg_roles WHERE rolname = current_user;",
        ),
    )
    _, _, replication, createdb, createrole = rows[0]
    assert replication == "t", "warehouse_reader lacks REPLICATION; logical decoding cannot work"
    assert createdb == "f" and createrole == "f"

    assert db.scalar(reader, "SELECT count(*) FROM res_partner;") is not None, "SELECT must work"

    denials = {}
    for label, statement in (
        ("INSERT", "INSERT INTO res_partner (name) VALUES ('qa-should-fail');"),
        ("UPDATE", "UPDATE res_partner SET name = 'qa-should-fail' WHERE id = 1;"),
        ("DELETE", "DELETE FROM res_partner WHERE id = 1;"),
        ("CREATE TABLE", "CREATE TABLE qa_should_fail (id int);"),
        ("CREATE TEMP TABLE", "CREATE TEMP TABLE qa_should_fail_tmp (id int);"),
    ):
        rc, _, err = db.execute(reader, statement)
        denials[label] = err
        assert rc != 0, "%s SUCCEEDED as warehouse_reader -- there is a write path into Odoo" % label
    evidence.add(
        "denials, verbatim",
        "\n".join("%-18s %s" % (k, v.splitlines()[0]) for k, v in denials.items()),
    )


def test_warehouse_role_model_matches_contract_05(warehouse_up, evidence):
    """Four roles, and only one of them a superuser -- and nothing queries data as that one.

    Contract 05 §A: a single shared identity would make every isolation test in this project green
    and worthless, because RLS is never evaluated for a SUPERUSER or a BYPASSRLS role.
    """
    grid = db.grid(
        db.warehouse_admin(),
        "SELECT rolname, rolsuper, rolbypassrls, rolcanlogin FROM pg_roles "
        "WHERE rolname IN ('warehouse_admin','warehouse','warehouse_loader','warehouse_rls') "
        "ORDER BY rolname;",
    )
    evidence.add("warehouse roles", grid)
    rows = db.query(
        db.warehouse_admin(),
        "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles "
        "WHERE rolname IN ('warehouse_admin','warehouse','warehouse_loader','warehouse_rls');",
    )
    attrs = dict((r[0], (r[1] == "t", r[2] == "t")) for r in rows)
    assert set(attrs) == {"warehouse_admin", "warehouse", "warehouse_loader", "warehouse_rls"}, attrs
    for role in ("warehouse", "warehouse_loader", "warehouse_rls"):
        superuser, bypass = attrs[role]
        assert not superuser and not bypass, (
            "%s is superuser=%s bypassrls=%s. RLS is not evaluated for it, so every isolation "
            "assertion made through it is vacuous." % (role, superuser, bypass)
        )
    assert attrs["warehouse_admin"][0], "warehouse_admin is expected to be the superuser"

    # And each of the three non-superuser roles authenticates, so the tests below are not silently
    # falling back to a single identity.
    for target in (db.warehouse_dbt(), db.warehouse_loader(), db.warehouse_rls()):
        identity = db.role_identity(target)
        assert identity.user == target.user, "%s authenticated as %s" % (target.user, identity.user)
        assert identity.rls_applies


def test_loader_cannot_create_or_mutate_the_landing_zone(warehouse_up, evidence):
    """Append-only is enforced by the grant, not by the loader's discipline (contract 05 §A.1)."""
    loader = db.warehouse_loader()
    evidence.add("identity", db.role_identity(loader).grid)

    rc, _, err = db.execute(loader, "CREATE TABLE raw.qa_should_fail (id int);")
    evidence.add("CREATE TABLE in schema raw", err or "(no error -- THIS IS THE BUG)")
    assert rc != 0, "warehouse_loader can CREATE in schema raw; it could land an unclassified column"

    grid = db.grid(
        loader,
        "SELECT has_table_privilege('raw.res_partner','SELECT') AS sel, "
        "has_table_privilege('raw.res_partner','INSERT') AS ins, "
        "has_table_privilege('raw.res_partner','UPDATE') AS upd, "
        "has_table_privilege('raw.res_partner','DELETE') AS del;",
    )
    evidence.add("privileges on raw.res_partner", grid)
    sel, ins, upd, dele = db.query(
        loader,
        "SELECT has_table_privilege('raw.res_partner','SELECT'), "
        "has_table_privilege('raw.res_partner','INSERT'), "
        "has_table_privilege('raw.res_partner','UPDATE'), "
        "has_table_privilege('raw.res_partner','DELETE');",
    )[0]
    assert (sel, ins) == ("t", "t")
    assert upd == "f", "warehouse_loader holds UPDATE on raw.res_partner; append-only is not enforced"
    assert dele == "f", "warehouse_loader holds DELETE on raw.res_partner; a tombstone could be erased"


def test_landing_tables_carry_the_contract_05_metadata_columns(warehouse_up, evidence):
    """`_ingested_at`, `_op`, `_tenant_id`, `_lsn` on every `raw.*` table."""
    required = {"_ingested_at", "_op", "_tenant_id", "_lsn"}
    rows = db.query(
        db.warehouse_admin(),
        "SELECT table_name, string_agg(column_name, ',') FROM information_schema.columns "
        "WHERE table_schema = 'raw' AND left(column_name, 1) = '_' "
        "GROUP BY table_name ORDER BY table_name;",
    )
    report = []
    problems = []
    for table, cols in rows:
        present = set(cols.split(","))
        report.append("%-20s %s" % (table, ",".join(sorted(present))))
        if not required.issubset(present):
            problems.append("%s is missing %s" % (table, sorted(required - present)))
    evidence.add("underscore-prefixed columns per raw table", "\n".join(report))
    assert rows, "no raw.* tables exist"
    assert not problems, problems
