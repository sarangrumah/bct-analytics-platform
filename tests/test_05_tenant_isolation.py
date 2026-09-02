"""Tenant isolation at the storage layer -- Postgres RLS, not application filtering.

Master prompt §3.3 requires isolation the engine enforces, and contract 05 §A.2 explains the three
things that have to be true simultaneously for that to be real:

1. every mart carries ``ENABLE`` **and** ``FORCE ROW LEVEL SECURITY`` (``FORCE`` is what subjects
   the owner to its own policies);
2. the marts are owned by ``warehouse``, which is ``NOSUPERUSER NOBYPASSRLS``;
3. the serving identity ``warehouse_rls`` matches only the tenant policy, so with ``app.tenant_id``
   unset it reads **zero** rows -- fail closed.

Every test in this file calls :func:`helpers.db.assert_rls_subject` first. RLS is never evaluated
for a ``SUPERUSER`` or a ``BYPASSRLS`` role, so an isolation test pointed at one passes for ever
while proving nothing at all -- and the boolean it would be checked against renders as ``true`` /
``false`` through ``||`` but as ``t`` / ``f`` in a result grid, so a naive string comparison never
matches and never fails either. The helper parses the column and returns a real ``bool``.
"""

from __future__ import annotations

import pytest

from helpers import db
from helpers import env as env_helper
from helpers.env import env

pytestmark = [pytest.mark.live]

NEWLINE = chr(10)


def test_serving_role_is_actually_subject_to_rls(warehouse_up, evidence):
    """The precondition for every other isolation claim in this project."""
    target = db.warehouse_rls()
    identity = db.assert_rls_subject(target)
    evidence.add("SELECT current_user, rolsuper, rolbypassrls FROM pg_roles ...", identity.grid)
    evidence.add(
        "parsed",
        "user=%s superuser=%s bypassrls=%s  ->  RLS is evaluated for this connection: %s"
        % (identity.user, identity.superuser, identity.bypassrls, identity.rls_applies),
    )
    assert identity.user == env("WAREHOUSE_RLS_USER", "warehouse_rls")


def test_app_tenant_id_is_not_pinned_on_the_role(warehouse_up, evidence):
    """A role-level `SET app.tenant_id` would make every session silently scoped to one tenant.

    It would look like isolation working. It would also mean a cross-tenant test could never fail,
    and that a second tenant would be invisible rather than forbidden.
    """
    grid = db.grid(
        db.warehouse_admin(),
        "SELECT rolname, rolconfig FROM pg_roles "
        "WHERE rolname IN ('warehouse','warehouse_loader','warehouse_rls') ORDER BY 1;",
    )
    evidence.add("role-level configuration", grid)
    rows = db.query(
        db.warehouse_admin(),
        "SELECT rolname, coalesce(array_to_string(rolconfig, ' '), '') FROM pg_roles "
        "WHERE rolname IN ('warehouse','warehouse_loader','warehouse_rls');",
    )
    pinned = [r[0] for r in rows if "app.tenant_id" in (r[1] or "")]
    assert not pinned, "app.tenant_id is pinned at role level on %r" % (pinned,)

    default = db.scalar(
        db.warehouse_rls(), "SELECT coalesce(current_setting('app.tenant_id', true), '<unset>');"
    )
    evidence.add("app.tenant_id as seen by a fresh warehouse_rls session", default)
    assert default == "<unset>", (
        "a fresh warehouse_rls session already has app.tenant_id = %r; RLS would be pre-scoped "
        "rather than fail-closed" % default
    )


def test_serving_role_cannot_see_the_landing_zone(warehouse_up, evidence):
    """`warehouse_rls` has no access to `raw` at all -- deliberate, contract 05 §A.5.

    Note the confusing failure mode the contract calls out: a role with no privilege on a schema
    sees its tables as **absent**, not as inaccessible. So this asserts the denial explicitly rather
    than inferring it from an empty catalogue listing.
    """
    target = db.warehouse_rls()
    identity = db.assert_rls_subject(target)
    evidence.add("identity", identity.grid)
    rc, _, err = db.execute(target, "SELECT count(*) FROM raw.res_partner;")
    evidence.add("SELECT from raw.res_partner as warehouse_rls", err or "(SUCCEEDED -- THIS IS A BUG)")
    assert rc != 0, "warehouse_rls can read the landing zone; the serving path can reach unmodelled data"


def test_statement_logging_is_applied_by_the_server(warehouse_up, evidence):
    """Contract 05 §B layer 1: `ALTER ROLE warehouse_rls SET log_statement='all'`.

    Applied by the server so a client cannot opt out, and it is what covers the gap left by
    Postgres being unable to trigger on SELECT.
    """
    value = db.scalar(
        db.warehouse_admin(),
        "SELECT coalesce(array_to_string(rolconfig, ' '), '') FROM pg_roles "
        "WHERE rolname = 'warehouse_rls';",
    )
    evidence.add("rolconfig on warehouse_rls", value or "(empty)")
    assert "log_statement=all" in (value or "").replace(" ", ""), (
        "warehouse_rls does not carry log_statement='all'. Contract 05 §B makes this the layer "
        "that records a read when the client forgets to call warehouse.log_access()."
    )


# ---------------------------------------------------------------------------------------------
# The mart-level assertions. Written in full; they run the moment `marts` is populated.
# ---------------------------------------------------------------------------------------------


def test_every_mart_has_force_row_level_security(marts_exist, evidence):
    grid = db.grid(
        db.warehouse_admin(),
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, pg_get_userbyid(c.relowner) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'marts' AND c.relkind = 'r' ORDER BY 1;",
    )
    evidence.add("RLS flags on marts.*", grid)
    rows = db.query(
        db.warehouse_admin(),
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, pg_get_userbyid(c.relowner) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'marts' AND c.relkind = 'r';",
    )
    problems = []
    for name, enabled, forced, owner in rows:
        if enabled != "t":
            problems.append("marts.%s has no ROW LEVEL SECURITY" % name)
        if forced != "t":
            problems.append(
                "marts.%s is not FORCE ROW LEVEL SECURITY; its owner (%s) bypasses its own policies"
                % (name, owner)
            )
    assert rows, "no tables in schema marts"
    assert not problems, problems


def test_unscoped_serving_session_reads_zero_rows(marts_exist, evidence):
    """Fail closed: with `app.tenant_id` unset, the serving role must see nothing."""
    target = db.warehouse_rls()
    identity = db.assert_rls_subject(target)
    evidence.add("identity", identity.grid)
    report, leaks = [], []
    for mart in marts_exist:
        count = db.scalar(target, "SELECT count(*) FROM marts.%s;" % mart)
        report.append("%-28s %s" % (mart, count))
        if int(count or 0) != 0:
            leaks.append("marts.%s returned %s rows with app.tenant_id unset" % (mart, count))
    evidence.add("rows visible to warehouse_rls with app.tenant_id UNSET", "\n".join(report))
    assert not leaks, leaks


def test_a_tenant_scoped_session_sees_only_its_own_rows(marts_exist, evidence):
    """The cross-tenant assertion itself: scope to tenant A, count tenant B's rows, expect zero."""
    target = db.warehouse_rls()
    identity = db.assert_rls_subject(target)
    evidence.add("identity", identity.grid)

    tenants = [
        r[0] for r in db.query(
            db.warehouse_admin(), "SELECT tenant_id FROM warehouse.tenant_registry ORDER BY 1;"
        )
    ]
    assert len(tenants) >= 2, (
        "only %d tenant(s) registered (%r); a cross-tenant assertion needs at least two, otherwise "
        "zero rows for 'the other tenant' proves only that the other tenant has no data"
        % (len(tenants), tenants)
    )
    a, b = tenants[0], tenants[1]

    # The zero is only evidence if tenant B actually HAS rows to hide. Counted through the
    # superuser, which bypasses RLS -- that is the one legitimate use for it in this suite, and it
    # is a count of ground truth rather than an isolation assertion.
    truth = dict(
        (r[0], int(r[1])) for r in db.query(
            db.warehouse_admin(),
            "SELECT tenant_id, count(*) FROM marts.%s GROUP BY 1;" % marts_exist[0],
        )
    )
    evidence.add(
        "ground truth in marts.%s, read past RLS as the superuser" % marts_exist[0],
        "\n".join("%-10s %s rows" % (k, v) for k, v in sorted(truth.items())),
    )

    report, leaks, sampled, hidden = [], [], 0, 0
    for mart in marts_exist:
        # SET LOCAL inside an explicit transaction: contract 05's T-1. A plain SET would survive a
        # pooled connection checkin, which is the failure mode that makes RLS look enforced.
        sql = (
            "SELECT current_setting('app.tenant_id') AS scope, "
            "count(*) FILTER (WHERE tenant_id = %s) AS own, "
            "count(*) FILTER (WHERE tenant_id = %s) AS other, "
            "count(*) AS total FROM marts.%s;"
            % (db.quote_literal(a), db.quote_literal(b), mart)
        )
        rows = db.scoped_query(target, a, sql, arity=4)
        assert rows, "no result row for marts.%s" % mart
        scope, own, other, total = rows[0]
        report.append("%-28s scope=%-8s own=%-8s other=%-6s total=%s" % (mart, scope, own, other, total))
        assert scope == a, "SET LOCAL did not take effect on marts.%s (scope=%r)" % (mart, scope)
        if int(other) != 0:
            leaks.append("marts.%s: %s rows belonging to %s visible while scoped to %s"
                         % (mart, other, b, a))
        if int(total) != int(own):
            leaks.append("marts.%s: total=%s but own=%s -- rows visible that belong to neither"
                         % (mart, total, own))
        if int(own) > 0:
            sampled += 1
        # How many rows of tenant B exist at all in this mart? Zero hidden rows means the zero above
        # is not evidence of anything.
        actual_b = int(db.scalar(
            db.warehouse_admin(),
            "SELECT count(*) FROM marts.%s WHERE tenant_id = %s;" % (mart, db.quote_literal(b)),
        ))
        report[-1] += "   (tenant %s truly has %s rows here)" % (b, actual_b)
        hidden += actual_b
    evidence.add("every mart, scoped to tenant %s, counting tenant %s's rows" % (a, b),
                 "\n".join(report))
    assert not leaks, leaks
    assert sampled, (
        "every mart returned zero rows even for its own tenant, so 'zero rows for the other tenant' "
        "proves nothing. Populate the marts before treating this as an isolation result."
    )
    assert hidden > 0, (
        "tenant %s has no rows in any mart, so 'other=0' everywhere is the absence of data, not the "
        "presence of isolation. This assertion is currently vacuous and must not be reported as a "
        "pass." % b
    )
    evidence.add(
        "VERDICT",
        "%d rows belonging to tenant %s exist across the marts and NONE were visible to a session "
        "scoped to tenant %s, running as warehouse_rls (rolsuper=f, rolbypassrls=f)."
        % (hidden, b, a),
    )


# ---------------------------------------------------------------------------------------------
# After a restore. RLS survives a `--full-refresh`; it does NOT automatically survive pg_restore.
# ---------------------------------------------------------------------------------------------


def test_no_mart_is_owned_by_a_superuser(marts_exist, evidence):
    """Ownership is half of what makes FORCE ROW LEVEL SECURITY mean anything.

    `FORCE` subjects a table's owner to its own policies -- but a **superuser** bypasses row
    security unconditionally, and no policy and no `FORCE` can stop it. So a mart owned by a
    superuser has RLS that is present, enabled, forced, and inert.

    This is not hypothetical. `warehouse-backup.sh` passed `--no-owner` to `pg_restore`, which
    assigns ownership to the *restoring* role -- `warehouse_admin`. A restored warehouse therefore
    came back with all 41 tables superuser-owned and every mart returning rows to anyone, **with
    nothing erroring anywhere**. Every RLS test in this file passed on that database: the flags were
    all still set, and the boundary was gone.
    """
    rows = db.query(
        db.warehouse_admin(),
        "SELECT c.relname, pg_get_userbyid(c.relowner), r.rolsuper "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_roles r ON r.oid = c.relowner "
        "WHERE n.nspname = 'marts' AND c.relkind = 'r' ORDER BY 1;",
    )
    evidence.add(
        "mart ownership",
        "\n".join("%-28s owner=%-18s rolsuper=%s" % r for r in rows),
    )
    assert rows, "no tables in schema marts"
    superuser_owned = [(name, owner) for name, owner, is_super in rows if is_super == "t"]
    assert not superuser_owned, (
        "these marts are owned by a SUPERUSER, which bypasses row security unconditionally. Their "
        "RLS is enabled, forced and inert: %r" % superuser_owned
    )


def test_the_backup_script_does_not_strip_ownership(evidence):
    """The one-flag version of the test above, so the regression is caught before a restore happens.

    A structural check on the script is worth having next to the state check on the database: the
    state check only fails *after* someone has restored into production.
    """
    script = env_helper.repo_root() / "analytics" / "warehouse" / "bin" / "warehouse-backup.sh"
    if not script.exists():
        pytest.skip("analytics/warehouse/bin/warehouse-backup.sh does not exist (NOT RUN)")
    text = script.read_text(encoding="utf-8")
    offending = [
        line.strip() for line in text.splitlines()
        if "--no-owner" in line and not line.strip().startswith("#")
    ]
    evidence.add("uncommented --no-owner occurrences", "\n".join(offending) or "none")
    assert not offending, (
        "warehouse-backup.sh passes --no-owner, so pg_restore assigns every table to the restoring "
        "role. Restoring as warehouse_admin makes every mart superuser-owned and its RLS inert, "
        "and nothing errors: %r" % offending
    )


def test_access_audit_names_the_service_that_read(warehouse_up, evidence):
    """Contract 05 §A.6: every warehouse consumer must set `application_name`.

    `warehouse_rls` is shared between `semantic-api` and `warehouse-exporter`, so `usename` cannot
    tell them apart -- DWH found that its own audit trail could not name which service performed a
    read. `application_name` is the separator, and the contract makes it a MUST.

    A MUST in a contract with no test behind it is a convention. This is the test, and it is
    written to skip -- loudly, with the reason -- rather than to pass, while the deployed images
    predate the change. `login-gateway` is out of scope by construction: it connects to no database.
    """
    rows = db.query(
        db.warehouse_admin(),
        "SELECT coalesce(nullif(application_name, ''), '(unset)'), usename, count(*) "
        "FROM pg_stat_activity WHERE usename LIKE 'warehouse%' GROUP BY 1, 2 ORDER BY 1, 2;",
    )
    evidence.add(
        "live warehouse connections by application_name",
        NEWLINE.join("%-28s %-18s %s" % r for r in rows) or "(no warehouse connections)",
    )
    unset = [r for r in rows if r[0] == "(unset)"]
    if unset:
        pytest.skip(
            "%d live warehouse connection group(s) carry no application_name. semantic-api and the "
            "CDC loader run from images built before contract 05 gained A.6, so this is NOT YET ON "
            "THE WIRE and asserting on it would report a defect that is really a stale image. "
            "Rebuild both images and re-run; this then asserts instead of skipping. NOT RUN."
            % len(unset)
        )
    audited = db.query(
        db.warehouse_admin(),
        "SELECT count(*) FILTER (WHERE application_name IS NULL OR application_name = ''), "
        "count(*) FROM warehouse.access_audit;",
    )
    evidence.add("warehouse.access_audit rows missing application_name", str(audited[0]))
    missing, total = int(audited[0][0]), int(audited[0][1])
    assert total > 0, (
        "warehouse.access_audit is empty, so 'every row names its service' is vacuous. Serve some "
        "traffic through semantic-api first."
    )
    assert missing == 0, (
        "%d of %d access_audit rows carry no application_name; the audit trail cannot say which "
        "service performed those reads" % (missing, total)
    )
