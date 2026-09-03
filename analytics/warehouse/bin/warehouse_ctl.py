#!/usr/bin/env python3
"""warehouse_ctl — policy sync, landing-zone DDL, FDW wiring and the dev fixture load.

WHAT THIS IS, AND WHAT IT IS NOT
================================
This is the **Data Warehouse agent's** tooling. It runs inside the `dbt`
container (which already carries psycopg2 and can reach both databases), never
on the host, so it has no host Python dependency and its credentials come from
compose rather than from a shell.

It is **not the CDC loader.** `analytics/cdc/**` is the Backend agent's and is
the only thing that streams `pgoutput`. What lives here is the three jobs that
are unambiguously DWH's under the brief, plus one development affordance that
is labelled as such:

  sync-policy   Read `pdp.field.classification` out of Odoo and materialise
                `warehouse.column_policy`. This is the seam of contract 05:
                DWH writes the policy, Backend's loader executes it.
  gen-raw-ddl   Generate `raw.*` from that policy. DWH owns landing DDL
                because CREATE TABLE is where "no unclassified column can
                land" and "a secret column does not exist" stop being
                conventions and become structural facts.
  gen-fdw       Wire the reconciliation path: foreign tables over the Odoo
                database, as `warehouse_reader`, with an explicit column list
                that contains no `secret` column at all.
  load-fixture  A DEVELOPMENT SNAPSHOT LOAD. It applies the same policy
                through the same HMAC to populate `raw.*` so the marts can be
                built and tested before, and independently of, Backend's
                consumer. It is a fixture, not a pipeline: no WAL, no slot, no
                resumability. Backend's loader writes the same tables and
                supersedes it (its rows carry a real `_lsn`, which sorts after
                every fixture row).
  tombstone     Append `_op='D'` rows, to exercise the delete semantics ADR
                0001 requires be tested.
  verify        Assert every column about to land carries a policy row, and
                that no `hmac_sha256` transform points at a non-text column.

USAGE
    docker compose -p odoo19-bct -f ... run --rm --entrypoint python \\
        dbt /warehouse/bin/warehouse_ctl.py <command> [--tenant bct]
"""
from __future__ import annotations

import argparse
import csv
import pathlib
import os
import sys

import psycopg2
import psycopg2.extras

# ---------------------------------------------------------------------------
# The replicated source set.
#
# Deliberately explicit rather than "every table Odoo has". A warehouse that
# replicates whatever it finds inherits every future module's columns without
# a classification decision having been made about them, which is exactly the
# failure contract 01 exists to prevent.
#
# `res_users` is absent on purpose: no mart in the metric contract needs it,
# and it is the table that carries `password` and `totp_secret`. The `secret`
# exclusion is still proven — sale_order.access_token, account_move.access_token,
# account_move.inalterable_hash, pos_order.access_token and pos_order.ticket_code
# are all `secret` and all live on tables that ARE replicated.
# ---------------------------------------------------------------------------
SOURCE_TABLES: tuple[str, ...] = (
    "res_company",
    "res_partner",
    "product_template",
    "product_product",
    "operating_unit",
    "sale_order",
    "sale_order_line",
    "account_move",
    "account_move_line",
    "account_account",
    "stock_picking",
    "stock_move",
    "pos_order",
    "pos_order_line",
    "ppob_biller",
    "ppob_transaction",
)

# Physical table -> Odoo model, for looking the classification up. Odoo's own
# convention is dots to underscores, but pos.order.line -> pos_order_line is
# ambiguous in reverse (pos_order_line could be pos.order_line), so the map is
# written out rather than derived.
TABLE_TO_MODEL: dict[str, str] = {
    "res_company": "res.company",
    "res_partner": "res.partner",
    "product_template": "product.template",
    "product_product": "product.product",
    "operating_unit": "operating.unit",
    "sale_order": "sale.order",
    "sale_order_line": "sale.order.line",
    "account_move": "account.move",
    "account_move_line": "account.move.line",
    "account_account": "account.account",
    "stock_picking": "stock.picking",
    "stock_move": "stock.move",
    "pos_order": "pos.order",
    "pos_order_line": "pos.order.line",
    "ppob_biller": "ppob.biller",
    "ppob_transaction": "ppob.transaction",
}

# ---------------------------------------------------------------------------
# contract 05's class -> transform mapping. "Not negotiable" in the contract,
# so it is a constant here and a CHECK constraint in the database. Two places,
# because a constant can be edited and a constraint cannot be edited by
# accident.
# ---------------------------------------------------------------------------
CLASS_TO_TRANSFORM: dict[str, str] = {
    "public": "none",
    "internal": "none",
    "personal": "hmac_sha256",
    "sensitive": "hmac_sha256_nullable",
    "secret": "drop",
}

# ---------------------------------------------------------------------------
# Rulings recorded in contract 01 that the addon's committed seed has not yet
# caught up with. Each entry cites the commit that made the ruling and is
# announced loudly on every run, so it can never quietly become the DWH agent
# inventing a classification — which contract 01 forbids.
#
# Remove an entry the moment custom_pdp_core's CSV agrees; the script says so
# when that happens.
# ---------------------------------------------------------------------------
CONTRACT_01_OVERRIDES: dict[tuple[str, str], tuple[str, bool, str]] = {
    # (model, field): (pdp_class, drop_to_null, why)
    #
    # EMPTY, and that is the correct state. The mechanism stays because it was
    # needed once and will be needed again: on 2026-08-31 contract 01 was
    # amended (Lead ruling 064d3c2) to reclassify res.partner.barcode from
    # `personal` to `sensitive` + drop_to_null, and for a short window the
    # ruling existed in the contract while custom_pdp_core's committed seed
    # still said `personal`. Platform-Addons has since regenerated the seed and
    # the registry agrees, so the override was removed rather than left to rot.
    #
    # RULES for adding one:
    #   * it must cite the Lead's ruling commit;
    #   * it is announced on every run, never silent;
    #   * the script says "NO LONGER NEEDED" the moment the registry catches
    #     up, so an override cannot quietly become the DWH agent inventing a
    #     classification -- which contract 01 forbids outright.
}

METADATA_COLUMNS = """  _row_id      bigint GENERATED ALWAYS AS IDENTITY,
  _ingested_at timestamptz NOT NULL DEFAULT now(),
  _op          char(1)     NOT NULL CHECK (_op IN ('I','U','D')),
  _tenant_id   text        NOT NULL,
  _lsn         pg_lsn"""

TEXTUAL_TYPES = {"text", "varchar", "bpchar", "char", "name", "citext"}


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------
def connect_odoo(database: str):
    """Read-only connection to the Odoo OLTP database.

    ALWAYS as warehouse_reader. That role holds SELECT + REPLICATION and
    nothing else (contract 04 §2), so "never write to the Odoo database" is
    guaranteed by the role rather than by this code being careful.
    """
    return psycopg2.connect(
        host=os.environ.get("ODOO_PG_HOST", "postgres"),
        port=int(os.environ.get("ODOO_PG_PORT", "5432")),
        dbname=database,
        user=os.environ["WAREHOUSE_READER_USER"],
        password=os.environ["WAREHOUSE_READER_PASSWORD"],
        application_name="warehouse_ctl",
    )


def connect_warehouse(admin: bool = False):
    """Connection to the warehouse.

    `admin` is needed only for CREATE SERVER / CREATE USER MAPPING, which are
    superuser-only operations. Everything else runs as `warehouse`.
    """
    user = os.environ["WAREHOUSE_ADMIN_USER"] if admin else os.environ["WAREHOUSE_DB_USER"]
    pwd = os.environ["WAREHOUSE_ADMIN_PASSWORD"] if admin else os.environ["WAREHOUSE_DB_PASSWORD"]
    return psycopg2.connect(
        host=os.environ.get("WAREHOUSE_HOST", "warehouse-db"),
        port=int(os.environ.get("WAREHOUSE_PORT", "5432")),
        dbname=os.environ["WAREHOUSE_DB"],
        user=user,
        password=pwd,
        application_name="warehouse_ctl",
    )


def tenants(wh) -> list[dict]:
    with wh.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT tenant_id, source_database, mask_salt_env, is_test_tenant "
            "FROM warehouse.tenant_registry WHERE active ORDER BY is_test_tenant, tenant_id"
        )
        return list(cur.fetchall())


def salt_for(tenant: dict) -> str:
    """Resolve a tenant's HMAC salt from the environment.

    The registry stores the NAME of the variable, never the value (contract 01:
    the salt lives in SOPS, never in a file, never in git). An absent or empty
    salt is fatal — degrading to an unkeyed hash is item 11 of the pinned
    construction and is never acceptable.
    """
    env_name = tenant["mask_salt_env"]
    salt = os.environ.get(env_name, "")
    if not salt:
        # The documented fallback order from custom_pdp_masking §2: tenant
        # variable, then DEFAULT.
        salt = os.environ.get("WAREHOUSE_MASK_SALT_DEFAULT", "")
    if not salt:
        sys.exit(
            f"FATAL: no HMAC salt for tenant {tenant['tenant_id']}. Expected {env_name} "
            f"or WAREHOUSE_MASK_SALT_DEFAULT in the environment. Refusing to load: an "
            f"unkeyed or empty-salt digest is not masking."
        )
    return salt


# ---------------------------------------------------------------------------
# Source introspection
# ---------------------------------------------------------------------------
def source_columns(odoo, table: str) -> list[dict]:
    """Physical columns of a source table, with their exact rendered types.

    format_type() rather than information_schema.data_type because the latter
    renders `character varying` without its length and `numeric` without its
    precision, and the landing table has to be able to hold what the source
    holds.
    """
    sql = """
        SELECT a.attname                                  AS column_name,
               format_type(a.atttypid, a.atttypmod)       AS col_type,
               t.typname                                  AS udt_name,
               a.attnum                                   AS ordinal
        FROM pg_attribute a
        JOIN pg_class     c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_type      t ON t.oid = a.atttypid
        WHERE n.nspname = 'public'
          AND c.relname = %s
          AND a.attnum > 0
          AND NOT a.attisdropped
        ORDER BY a.attnum
    """
    with odoo.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (table,))
        return list(cur.fetchall())


def classification_map(odoo) -> dict[tuple[str, str], dict]:
    """The active rows of custom_pdp_core's registry, keyed by (model, field).

    Read straight from the table rather than through JSON-RPC. The registry's
    MODULE_KNOWLEDGE documents a JSON-RPC surface for the loader; DWH is
    already connected to this database read-only for reconciliation, and a
    second protocol would be a second way for the two to disagree.
    """
    with odoo.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT model_name, field_name, pdp_class, drop_to_null "
            "FROM pdp_field_classification WHERE active"
        )
        rows = cur.fetchall()
    out: dict[tuple[str, str], dict] = {}
    for r in rows:
        out[(r["model_name"], r["field_name"])] = dict(r)

    # Apply the Lead's contract 01 rulings, loudly.
    for (model, field), (klass, drop_to_null, why) in CONTRACT_01_OVERRIDES.items():
        current = out.get((model, field))
        if current is None:
            print(f"  NOTE  override for {model}.{field} targets a column absent from the registry; skipped")
            continue
        if current["pdp_class"] == klass and bool(current["drop_to_null"]) == drop_to_null:
            print(
                f"  NOTE  contract-01 override for {model}.{field} is NO LONGER NEEDED - "
                f"custom_pdp_core's seed already agrees. Remove it from CONTRACT_01_OVERRIDES."
            )
            continue
        print(
            f"  OVERRIDE  {model}.{field}: registry says "
            f"{current['pdp_class']}/drop_to_null={current['drop_to_null']}, "
            f"contract 01 says {klass}/drop_to_null={drop_to_null}."
        )
        print(f"            {why}")
        print(
            "            The addon seed (addons/custom_pdp_core/data/pdp.field.classification.csv) "
            "has NOT caught up. Platform-Addons must regenerate it."
        )
        out[(model, field)] = {
            "model_name": model,
            "field_name": field,
            "pdp_class": klass,
            "drop_to_null": drop_to_null,
        }
    return out


def resolve_policy(odoo) -> tuple[list[tuple], list[str]]:
    """Build the column_policy rows, and the list of unclassified columns.

    An unclassified column is returned rather than defaulted. Contract 01 is
    explicit that unclassified is a hard failure and never a silent `public`.
    """
    cmap = classification_map(odoo)
    rows: list[tuple] = []
    unclassified: list[str] = []
    type_violations: list[str] = []

    for table in SOURCE_TABLES:
        model = TABLE_TO_MODEL[table]
        for col in source_columns(odoo, table):
            hit = cmap.get((model, col["column_name"]))
            if hit is None:
                unclassified.append(f"{model}.{col['column_name']} (physical {table}.{col['column_name']})")
                continue
            klass = hit["pdp_class"]
            transform = CLASS_TO_TRANSFORM[klass]
            mask_null = bool(hit["drop_to_null"]) and klass == "sensitive"

            # THE GUARD THE LEAD MADE BINDING (contract 01, general rule 2).
            # "Hard-fail on unclassified" alone would not have caught
            # res.partner.barcode: it WAS classified, as `personal`, but its
            # physical type is jsonb and the pinned HMAC construction takes
            # str or None and nothing else. A digest transform pointed at a
            # non-text column is a contract error, not a loader bug.
            if (
                transform.startswith("hmac")
                and not mask_null
                and col["udt_name"] not in TEXTUAL_TYPES
            ):
                type_violations.append(
                    f"{model}.{col['column_name']}: class={klass} -> {transform}, "
                    f"but the physical type is {col['col_type']} ({col['udt_name']}), not text"
                )
                continue
            rows.append((table, col["column_name"], klass, transform, mask_null))

    if type_violations:
        print("\nFATAL: a digest transform points at a non-text column.", file=sys.stderr)
        for v in type_violations:
            print(f"  - {v}", file=sys.stderr)
        print(
            "\nThis is a contract 01 question, not a loader bug. Either the column is "
            "reclassified sensitive+drop_to_null, or contract 01 pins an exact text "
            "rendering. Escalate; do not cast it here.",
            file=sys.stderr,
        )
        sys.exit(3)

    return rows, unclassified


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_sync_policy(args) -> int:
    wh = connect_warehouse()
    src_db = os.environ.get("ODOO_DB_NAME", "bct")
    odoo = connect_odoo(src_db)
    print(f"==> reading pdp.field.classification from Odoo database {src_db}")
    rows, unclassified = resolve_policy(odoo)

    if unclassified:
        print("\nFATAL: columns with no classification row.", file=sys.stderr)
        for u in unclassified:
            print(f"  - {u}", file=sys.stderr)
        print(
            "\nContract 01: unclassified is a hard failure, never a silent default to "
            "`public`. Add the rows to custom_pdp_core's seed and reinstall the module.",
            file=sys.stderr,
        )
        return 2

    with wh.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO warehouse.column_policy "
            "(source_table, source_column, pdp_class, transform, mask_null) VALUES %s "
            "ON CONFLICT (source_table, source_column) DO UPDATE SET "
            "  pdp_class = EXCLUDED.pdp_class, transform = EXCLUDED.transform, "
            "  mask_null = EXCLUDED.mask_null, updated_at = now()",
            rows,
        )
        # Remove rows for columns that no longer exist upstream. A stale policy
        # row is not harmless: it makes a dropped column look classified and
        # hides the schema change that ADR 0001 says must be loud.
        # Fully parameterised row comparison rather than a VALUES list built by
        # string interpolation. The interpolated version used cur.mogrify() and
        # was safe, but a security-reviewed file should not contain a SQL
        # statement assembled with % at all - the next person to edit it may not
        # keep the mogrify.
        cur.execute(
            "DELETE FROM warehouse.column_policy p "
            "WHERE (p.source_table, p.source_column) NOT IN %s",
            (tuple((r[0], r[1]) for r in rows),),
        )
        removed = cur.rowcount
    wh.commit()

    with wh.cursor() as cur:
        cur.execute("SELECT pdp_class, count(*) FROM warehouse.column_policy GROUP BY 1 ORDER BY 1")
        counts = cur.fetchall()
    print(f"==> warehouse.column_policy: {len(rows)} rows upserted, {removed} stale rows removed")
    for klass, n in counts:
        print(f"    {klass:<10} {n}")
    return 0


def _landing_ddl(odoo, wh, table: str) -> str:
    with wh.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT source_column, pdp_class, transform, mask_null "
            "FROM warehouse.column_policy WHERE source_table = %s",
            (table,),
        )
        pol = {r["source_column"]: r for r in cur.fetchall()}

    lines: list[str] = []
    dropped: list[str] = []
    for col in source_columns(odoo, table):
        p = pol.get(col["column_name"])
        if p is None:
            raise SystemExit(
                f"FATAL: {table}.{col['column_name']} has no policy row. Run sync-policy first; "
                f"if it still fails, the column is unclassified and that is a hard failure."
            )
        if p["transform"] == "drop":
            # NOT "selected and discarded" — the column has no name in the
            # landing table at all, so nothing downstream can ask for it.
            dropped.append(f"{col['column_name']} ({p['pdp_class']})")
            continue
        if p["transform"].startswith("hmac") and not p["mask_null"]:
            coltype = "text"            # a 64-character lowercase hex digest
        else:
            coltype = col["col_type"]   # verbatim, or always-NULL for mask_null
        lines.append(f'  "{col["column_name"]}" {coltype},')

    ddl = [
        f"-- raw.{table} — GENERATED by warehouse_ctl.py gen-raw-ddl. Do not hand-edit.",
    ]
    if dropped:
        ddl.append(f"-- `secret` columns structurally absent: {', '.join(dropped)}")
    ddl += [
        f"CREATE TABLE IF NOT EXISTS raw.{table} (",
        *lines,
        METADATA_COLUMNS,
        ");",
        # Contract 05: "Ordering key is (_tenant_id, <pk>, _lsn)". Every Odoo
        # table has an integer `id`, so the pk is always `id`.
        f"CREATE INDEX IF NOT EXISTS {table}_order_idx ON raw.{table} (_tenant_id, id, _lsn);",
        f"CREATE INDEX IF NOT EXISTS {table}_ingested_idx ON raw.{table} (_tenant_id, _ingested_at);",
        # EXACTLY-ONCE LANDING, enforced by the storage layer rather than only
        # by the loader. Logical replication is at-least-once: a consumer that
        # dies between committing the warehouse transaction and sending
        # feedback gets those changes redelivered. Backend now floors the
        # stream at max(_lsn) already landed, but that is the loader policing
        # itself; this makes the database refuse the second copy.
        #
        # PARTIAL, and the predicate is the whole point. A plain UNIQUE on
        # (_tenant_id, id, _op, _lsn) would also forbid re-running a SNAPSHOT,
        # because every fixture/backfill row carries _lsn '0/0' and a second
        # full snapshot legitimately re-appends the same keys. ADR 0001
        # requires the pipeline be re-seedable from snapshot, so that must stay
        # possible. Measured before choosing: raw.res_partner today holds 47
        # duplicate groups at '0/0' (re-runs, correct) and 2 above it (genuine
        # redelivery, the incident). The predicate separates exactly those.
        f"""DO $ux$
BEGIN
  BEGIN
    CREATE UNIQUE INDEX IF NOT EXISTS {table}_cdc_change_uidx
      ON raw.{table} (_tenant_id, id, _op, _lsn)
      WHERE _lsn <> '0/0'::pg_lsn;
  EXCEPTION WHEN unique_violation THEN
    -- Do NOT fail the whole DDL run: a routine `make up-analytics` should not
    -- break because of rows that landed before this control existed. But do
    -- not pass silently either - the guarantee is absent for this table until
    -- somebody removes the duplicates, and `warehouse_ctl.py verify` reports
    -- it as missing every time it runs.
    RAISE WARNING 'raw.{table}: exactly-once index NOT created - pre-existing duplicate CDC rows. Guarantee absent until they are removed; see warehouse_ctl.py verify.';
  END;
END
$ux$;""",
    ]

    # SCHEMA EVOLUTION. `CREATE TABLE IF NOT EXISTS` is a no-op once the table
    # exists, so a source column added after the landing table was first built
    # would never appear in raw -- and the loader's INSERT, which names every
    # policy column, would fail with "column ... does not exist". That is not
    # hypothetical: installing the odoo-platform addon suite took res_company
    # from 121 classified columns to 186, and this is where it surfaced.
    #
    # ADD COLUMN IF NOT EXISTS is additive and idempotent. It deliberately does
    # NOT drop anything: a column that has since been reclassified `secret` is
    # left in place and caught by the leak assertion in cmd_gen_raw_ddl, which
    # is the loud failure we want rather than a silent DROP of landed data.
    ddl += [
        f'ALTER TABLE raw.{table} ADD COLUMN IF NOT EXISTS {line.strip().rstrip(",")};'
        for line in lines
    ]
    return "\n".join(ddl)


# ---------------------------------------------------------------------------
# import-policy — the classification path for a client who is NOT on Odoo.
# ---------------------------------------------------------------------------
#: pdp_class -> (transform, mask_null). The database enforces this pairing with
#: column_policy_class_transform_ck, so deriving it here rather than asking for
#: it removes a whole class of mistake: a column classified correctly and
#: transformed wrongly. `sensitive` is the one class with a real choice, and
#: --nullable in the CSV picks the second form.
_CLASS_TRANSFORM = {
    "public":    ("none", False),
    "internal":  ("none", False),
    "personal":  ("hmac_sha256", False),
    "sensitive": ("hmac_sha256", False),
    "secret":    ("drop", False),
}


def cmd_import_policy(args) -> int:
    """Load a column classification from CSV, for a source that is not Odoo.

    WHY THIS EXISTS. sync-policy reads pdp.field.classification out of an Odoo
    database. A client who subscribes only to ATHERA Insight brings their own
    application and has no such table -- but the CDC loader still hard-exits
    with code 3 on any unclassified column, and that refusal is the point:
    unclassified data must not land. So the classification has to come from
    somewhere, and for these clients it comes from a file their onboarding
    produces.

    Same table, same constraints, same fail-closed posture. This is an
    additional SOURCE for warehouse.column_policy, not a second policy system.

    CSV columns: source_table, source_column, pdp_class [, nullable]
    `nullable` is only meaningful for pdp_class=sensitive.
    """
    path = pathlib.Path(args.file)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 2

    rows, bad = [], []
    seen_tables = set()
    with path.open(newline="", encoding="utf-8") as fh:
        for lineno, rec in enumerate(csv.DictReader(fh), start=2):
            table = (rec.get("source_table") or "").strip()
            column = (rec.get("source_column") or "").strip()
            klass = (rec.get("pdp_class") or "").strip().lower()
            nullable = (rec.get("nullable") or "").strip().lower() in ("1", "true", "yes")

            if not table or not column:
                bad.append(f"line {lineno}: source_table and source_column are both required")
                continue
            if klass not in _CLASS_TRANSFORM:
                bad.append(
                    f"line {lineno}: {table}.{column} has pdp_class {klass!r}; "
                    f"must be one of {', '.join(sorted(_CLASS_TRANSFORM))}"
                )
                continue

            transform, mask_null = _CLASS_TRANSFORM[klass]
            if klass == "sensitive" and nullable:
                transform, mask_null = "hmac_sha256_nullable", True
            elif nullable:
                bad.append(
                    f"line {lineno}: {table}.{column} sets nullable but pdp_class is "
                    f"{klass!r}; only `sensitive` may be nullable"
                )
                continue

            seen_tables.add(table)
            rows.append((table, column, klass, transform, mask_null))

    if bad:
        # Every fault at once. Reporting the first and exiting would make
        # classifying a real schema an exercise in re-running the command.
        print(f"\nFATAL: {len(bad)} problem(s) in {path}:", file=sys.stderr)
        for b in bad:
            print(f"  - {b}", file=sys.stderr)
        print(
            "\nContract 01: an unclassified or mis-classified column is a hard failure, "
            "never a silent default to `public`.",
            file=sys.stderr,
        )
        return 2
    if not rows:
        print(f"{path} contained no rows", file=sys.stderr)
        return 2

    wh = connect_warehouse()
    with wh.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO warehouse.column_policy "
            "(source_table, source_column, pdp_class, transform, mask_null) VALUES %s "
            "ON CONFLICT (source_table, source_column) DO UPDATE SET "
            "  pdp_class = EXCLUDED.pdp_class, transform = EXCLUDED.transform, "
            "  mask_null = EXCLUDED.mask_null, updated_at = now()",
            rows,
        )
        # Stale rows are removed ONLY for the tables this file names. A global
        # sweep here would delete the Odoo tenants' policy on the first import
        # for an external client -- the two share this table, and each source
        # is only authoritative for its own tables.
        # nosemgrep: bct-python-sql-string-interpolation  # %s and VALUES %s are bound psycopg2 params, not Python string interpolation
        cur.execute(
            "DELETE FROM warehouse.column_policy p "
            "WHERE p.source_table = ANY(%s) "
            "  AND NOT EXISTS (SELECT 1 FROM (VALUES %s) AS v(t, c) "
            "                   WHERE v.t = p.source_table AND v.c = p.source_column)"
            % ("%s", ", ".join(["(%s, %s)"] * len(rows))),
            [list(seen_tables)] + [x for r in rows for x in (r[0], r[1])],
        )
        removed = cur.rowcount
        cur.execute(
            "SELECT pdp_class, count(*) FROM warehouse.column_policy "
            "WHERE source_table = ANY(%s) GROUP BY 1 ORDER BY 1",
            (list(seen_tables),),
        )
        by_class = cur.fetchall()
    wh.commit()

    print(f"==> warehouse.column_policy: {len(rows)} rows upserted from {path}, "
          f"{removed} stale rows removed")
    print(f"    tables: {', '.join(sorted(seen_tables))}")
    for klass, n in by_class:
        print(f"      {klass:<10} {n}")
    return 0


def cmd_gen_raw_ddl(args) -> int:
    wh = connect_warehouse()
    odoo = connect_odoo(os.environ.get("ODOO_DB_NAME", "bct"))
    stmts = [_landing_ddl(odoo, wh, t) for t in SOURCE_TABLES]
    sql = "\n\n".join(stmts)
    if args.print_only:
        print(sql)
        return 0
    with wh.cursor() as cur:
        cur.execute(sql)
    wh.commit()
    with wh.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='raw'"
        )
        n = cur.fetchone()[0]
    print(f"==> raw schema: {n} landing tables present")

    # Prove the `secret` exclusion rather than assert it.
    with wh.cursor() as cur:
        cur.execute(
            """
            SELECT p.source_table, p.source_column
            FROM warehouse.column_policy p
            JOIN information_schema.columns c
              ON c.table_schema = 'raw'
             AND c.table_name   = p.source_table
             AND c.column_name  = p.source_column
            WHERE p.pdp_class = 'secret'
            """
        )
        leaked = cur.fetchall()
    if leaked:
        print(f"FATAL: secret columns present in raw: {leaked}", file=sys.stderr)
        return 4
    with wh.cursor() as cur:
        cur.execute("SELECT count(*) FROM warehouse.column_policy WHERE pdp_class='secret'")
        n_secret = cur.fetchone()[0]
    # ORPHANS. gen-raw-ddl only ever ADDS columns, so a source column that is
    # renamed or removed leaves its old column behind in raw. Contract 01 makes an
    # unclassified landed column a hard failure, and that is exactly what an orphan
    # becomes, so they cannot simply be ignored.
    #
    # An empty orphan is dropped: it carries nothing, and leaving it would fail the
    # contract for no gain. An orphan WITH DATA is never dropped here -- the tool
    # does not get to decide that landed data is worthless. It is reported with the
    # statement to run, and the command fails so the pipeline stops rather than
    # continuing in a state contract 01 forbids.
    with wh.cursor() as cur:
        cur.execute("""
            SELECT c.table_name, c.column_name
            FROM information_schema.columns c
            WHERE c.table_schema = 'raw'
              -- metadata columns are ours, not the source's
              AND left(c.column_name, 1) <> '_'
              AND NOT EXISTS (
                SELECT 1 FROM warehouse.column_policy p
                WHERE p.source_table = c.table_name AND p.source_column = c.column_name)
            ORDER BY 1, 2
        """)
        orphans = cur.fetchall()

    dropped, populated = [], []
    for table, col in orphans:
        with wh.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM raw.{table} WHERE "{col}" IS NOT NULL')
            if cur.fetchone()[0]:
                populated.append((table, col, "not empty"))
            else:
                # Dependants matter. dbt's staging and marts views select from
                # raw and are rebuilt from source on every run, so cascading
                # through them costs nothing. Anything else is somebody's object
                # and a blanket CASCADE would take it out silently.
                cur.execute("""
                    SELECT DISTINCT dn.nspname, dc.relname, dc.relkind
                    FROM pg_depend d
                    JOIN pg_rewrite r  ON r.oid = d.objid
                    JOIN pg_class   dc ON dc.oid = r.ev_class
                    JOIN pg_namespace dn ON dn.oid = dc.relnamespace
                    WHERE d.refobjid = %s::regclass
                      AND d.refobjsubid = (
                        SELECT attnum FROM pg_attribute
                        WHERE attrelid = %s::regclass AND attname = %s)
                """, (f"raw.{table}", f"raw.{table}", col))
                deps = cur.fetchall()
                foreign = [f"{s}.{r}" for s, r, k in deps
                           if s not in ("staging", "marts") or k != "v"]
                if foreign:
                    populated.append((table, col, "depended on by " + ", ".join(foreign)))
                    continue
                cur.execute(f'ALTER TABLE raw.{table} DROP COLUMN "{col}" CASCADE')
                rebuilt = sorted({f"{s}.{r}" for s, r, _ in deps})
                dropped.append(f"{table}.{col}"
                               + (f" (+{len(rebuilt)} dbt view(s) dbt will rebuild)" if rebuilt else ""))
    wh.commit()

    if dropped:
        print(f"==> dropped {len(dropped)} empty orphan column(s): {', '.join(dropped)}")
    if populated:
        print("FATAL: these landed columns have no policy row and were not dropped.",
              file=sys.stderr)
        print("       They are not dropped automatically. Decide, then run:", file=sys.stderr)
        for table, col, why in populated:
            print(f'         -- {why}', file=sys.stderr)
            print(f'         ALTER TABLE raw.{table} DROP COLUMN "{col}" CASCADE;', file=sys.stderr)
        return 5

    print(f"==> {n_secret} `secret` columns exist in the policy and NONE of them exists in raw")
    return 0


def cmd_gen_fdw(args) -> int:
    """Create one foreign server + schema per tenant over the Odoo database.

    Used by reconciliation (a mart must be compared against Odoo, not against
    a control total the warehouse computed for itself) and by load-fixture.

    Containment, both enforced rather than documented:
      * the user mapping is warehouse_reader, which cannot write to Odoo;
      * the foreign tables are created with an EXPLICIT column list from the
        policy, so no `secret` column exists as a name the warehouse can type.
    """
    wh_admin = connect_warehouse(admin=True)
    wh = connect_warehouse()
    odoo_host = os.environ.get("ODOO_PG_HOST", "postgres")
    odoo_port = os.environ.get("ODOO_PG_PORT", "5432")
    reader = os.environ["WAREHOUSE_READER_USER"]
    reader_pw = os.environ["WAREHOUSE_READER_PASSWORD"]
    wh_user = os.environ["WAREHOUSE_DB_USER"]

    for t in tenants(wh):
        tid = t["tenant_id"]
        server = f"odoo_src_{tid}"
        schema = f"src_{tid}"
        odoo = connect_odoo(t["source_database"])
        with wh_admin.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_foreign_server WHERE srvname = %s", (server,)
            )
            if not cur.fetchone():
                cur.execute(
                    f"CREATE SERVER {server} FOREIGN DATA WRAPPER postgres_fdw "
                    f"OPTIONS (host %s, port %s, dbname %s, "
                    # updatable=false is belt-and-braces on top of a role that
                    # cannot write: postgres_fdw will refuse the statement
                    # locally before it ever reaches Odoo.
                    f"        updatable 'false', fetch_size '10000')",
                    (odoo_host, odoo_port, t["source_database"]),
                )
            cur.execute(
                "SELECT 1 FROM pg_user_mappings WHERE srvname = %s AND usename = %s",
                (server, wh_user),
            )
            if not cur.fetchone():
                cur.execute(
                    f"CREATE USER MAPPING FOR {wh_user} SERVER {server} "
                    f"OPTIONS (user %s, password %s)",
                    (reader, reader_pw),
                )
            cur.execute(f"GRANT USAGE ON FOREIGN SERVER {server} TO {wh_user}")
            # Created by the admin WITH AUTHORIZATION, not by `warehouse`
            # itself: CREATE SCHEMA needs CREATE on the database, and granting
            # the transform role that privilege would let it create schemas
            # outside the four contract 05 names for no benefit.
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema} AUTHORIZATION {wh_user}")
        wh_admin.commit()

        with wh.cursor() as cur:
            for table in SOURCE_TABLES:
                cur.execute(
                    "SELECT source_column, transform FROM warehouse.column_policy "
                    "WHERE source_table = %s AND transform <> 'drop'",
                    (table,),
                )
                allowed = {r[0] for r in cur.fetchall()}
                cols = [c for c in source_columns(odoo, table) if c["column_name"] in allowed]
                if not cols:
                    raise SystemExit(f"FATAL: no policy rows for {table}; run sync-policy first")
                coldef = ",\n".join(f'  "{c["column_name"]}" {c["col_type"]}' for c in cols)
                cur.execute(f"DROP FOREIGN TABLE IF EXISTS {schema}.{table} CASCADE")
                cur.execute(
                    f"CREATE FOREIGN TABLE {schema}.{table} (\n{coldef}\n) "
                    f"SERVER {server} OPTIONS (schema_name 'public', table_name '{table}')"
                )
        wh.commit()
        print(f"==> {schema}: {len(SOURCE_TABLES)} foreign tables over {t['source_database']} as {reader}")
    return 0


def _mask_expr(col: dict, pol: dict) -> str:
    """The SQL that applies one column's policy. This is the policy EXECUTED."""
    name = f'"{col["column_name"]}"'
    if pol["transform"] == "none":
        return name
    if pol["mask_null"]:
        # `sensitive` free text and the company-dependent jsonb: NULL, typed,
        # so the landing column keeps its shape and holds nothing.
        return f'NULL::{col["col_type"]}'
    # hmac_sha256 / hmac_sha256_nullable without mask_null.
    return f"warehouse.pdp_hmac({name}::text, %(salt)s)"


def cmd_load_fixture(args) -> int:
    wh = connect_warehouse()
    total = 0
    for t in tenants(wh):
        if args.tenant and t["tenant_id"] != args.tenant:
            continue
        tid = t["tenant_id"]
        salt = salt_for(t)
        odoo = connect_odoo(t["source_database"])
        schema = f"src_{tid}"
        print(f"==> loading tenant {tid} from {schema} (test tenant: {t['is_test_tenant']})")
        for table in SOURCE_TABLES:
            with wh.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT source_column, transform, mask_null FROM warehouse.column_policy "
                    "WHERE source_table = %s",
                    (table,),
                )
                pol = {r["source_column"]: r for r in cur.fetchall()}
            cols = [c for c in source_columns(odoo, table) if pol.get(c["column_name"], {}).get("transform") != "drop"]
            target = ", ".join(f'"{c["column_name"]}"' for c in cols)
            exprs = ", ".join(_mask_expr(c, pol[c["column_name"]]) for c in cols)
            # _lsn = '0/0', NOT NULL.
            #
            # A snapshot row has no WAL position, and NULL is the obvious way to
            # say so. It is the wrong way. Contract 05 makes
            # (_tenant_id, pk, _lsn) the ordering key and says nothing about
            # what a NULL means there, so every consumer has to decide for
            # itself: my raw_latest macro coalesces to '0/0' and gets the right
            # answer, but Backend's landing-growth metric read NULL as
            # "unordered" and its duplicate detector counted two different
            # NULL-bearing changes as one, because SQL treats NULLs as equal
            # for DISTINCT.
            #
            # '0/0' is the lowest possible pg_lsn, so it carries the same
            # precedence - every real CDC row supersedes every snapshot row for
            # the same key, which is what makes a re-snapshot safe to run over
            # live data - while making the ordering key a TOTAL order with no
            # NULL semantics for anyone to interpret. Found by Backend's
            # bct_cdc_landing_unordered_rows metric; fixed here rather than
            # worked around there.
            sql = (
                f"INSERT INTO raw.{table} ({target}, _op, _tenant_id, _lsn) "
                f"SELECT {exprs}, 'I', %(tenant)s, '0/0'::pg_lsn FROM {schema}.{table}"
            )
            with wh.cursor() as cur:
                cur.execute(sql, {"salt": salt, "tenant": tid})
                n = cur.rowcount
                # pipeline_state is the ONLY source of meta.last_refreshed_at.
                # Writing it here means the freshness a dashboard shows after a
                # fixture load is real metadata, not a fabricated timestamp.
                cur.execute(
                    "INSERT INTO warehouse.pipeline_state "
                    "  (tenant_id, source_table, last_success_at, rows_loaded, slot_name) "
                    "VALUES (%s, %s, now(), %s, NULL) "
                    "ON CONFLICT (tenant_id, source_table) DO UPDATE SET "
                    "  last_success_at = now(), "
                    "  rows_loaded = warehouse.pipeline_state.rows_loaded + EXCLUDED.rows_loaded, "
                    "  last_error = NULL, failure_count = 0",
                    (tid, table, n),
                )
            total += n
            print(f"    raw.{table:<22} {n:>6} rows")
        wh.commit()
    print(f"==> {total} rows landed")
    return 0


def cmd_tombstone(args) -> int:
    """Append `_op='D'` rows so the delete semantics can be exercised.

    ADR 0001: a decoded DELETE lands as a tombstone; the landing zone stays
    append-only; marts filter to the latest non-deleted version per key. This
    is what makes that testable without waiting for somebody to delete a real
    record in Odoo.
    """
    wh = connect_warehouse()
    with wh.cursor() as cur:
        cur.execute(
            f"INSERT INTO raw.{args.table} (id, _op, _tenant_id, _lsn) "
            f"SELECT %s, 'D', %s, '0/0'::pg_lsn",
            (args.id, args.tenant),
        )
    wh.commit()
    print(f"==> tombstone appended: raw.{args.table} id={args.id} tenant={args.tenant}")
    return 0


def cmd_verify(args) -> int:
    """Standalone re-check of the two invariants, for CI and for a human."""
    odoo = connect_odoo(os.environ.get("ODOO_DB_NAME", "bct"))
    rows, unclassified = resolve_policy(odoo)
    ok = True
    if unclassified:
        ok = False
        print("UNCLASSIFIED COLUMNS (hard failure per contract 01):", file=sys.stderr)
        for u in unclassified:
            print(f"  - {u}", file=sys.stderr)
    else:
        print(f"OK  every one of {len(rows)} replicated columns carries a classification")

    wh = connect_warehouse()
    with wh.cursor() as cur:
        cur.execute(
            "SELECT p.source_table, p.source_column FROM warehouse.column_policy p "
            "JOIN information_schema.columns c ON c.table_schema='raw' "
            "  AND c.table_name = p.source_table AND c.column_name = p.source_column "
            "WHERE p.pdp_class = 'secret'"
        )
        leaked = cur.fetchall()
    if leaked:
        ok = False
        print(f"SECRET COLUMNS PRESENT IN raw: {leaked}", file=sys.stderr)
    else:
        print("OK  no `secret`-class column exists as a warehouse column")

    # EXACTLY-ONCE COVERAGE. The partial unique index is created by
    # gen-raw-ddl, but it cannot be created on a table that already holds
    # duplicate CDC rows - and that failure is a WARNING there, so that a
    # routine bring-up is not broken by rows which landed before the control
    # existed. This is where the absence stops being a log line and becomes a
    # reported gap, every run, until somebody clears the duplicates.
    with wh.cursor() as cur:
        cur.execute(
            "SELECT t.tablename FROM pg_tables t WHERE t.schemaname = 'raw' "
            "AND NOT EXISTS (SELECT 1 FROM pg_indexes i WHERE i.schemaname = 'raw' "
            "  AND i.tablename = t.tablename AND i.indexname LIKE '%_cdc_change_uidx') "
            "ORDER BY 1"
        )
        unprotected = [r[0] for r in cur.fetchall()]
    if unprotected:
        ok = False
        print(
            f"NO EXACTLY-ONCE INDEX on {len(unprotected)} raw table(s): {', '.join(unprotected)}",
            file=sys.stderr,
        )
        print(
            "  A redelivered CDC change can land twice on these. Logical replication is "
            "at-least-once, so this is a real gap, not a theoretical one. Clear the duplicate "
            "rows (see the query in the message above) and re-run gen-raw-ddl.",
            file=sys.stderr,
        )
    else:
        print("OK  every raw table refuses a redelivered CDC change at the storage layer")

    # CONNECTION BUDGET. The warehouse's max_connections is a SHARED resource
    # and every consumer sizes its pool against it: semantic-api 16 (Backend,
    # derived in Warehouse.__init__), dbt DBT_THREADS+1, the CDC loader 3, the
    # exporter 3, plus ad-hoc psql. Nobody owns the total, which is exactly the
    # kind of number that is correct on the day it is written and wrong three
    # months later when one consumer is retuned in isolation.
    #
    # Checked here rather than documented in two repositories. Measured peak on
    # this warehouse during a full dbt build was 10 concurrent (dbt 5 of them,
    # against the 8 Backend budgeted), so there is real slack today - the point
    # is to notice the day there is not.
    # Each entry is (label, value, provenance). PROVENANCE IS PRINTED, because
    # the first version of this check was asymmetric and did not look it: dbt
    # was read from the environment and semantic-api was the literal 16. An
    # operator raising SEMANTIC_API_POOL_MAX - which is a documented knob, and
    # is exactly what Backend's own shed log tells them to raise when the pool
    # saturates - would have left this reporting "OK, 6 spare" while the real
    # claim was 47 against 37. Oversubscribed by 10, reported healthy, in the
    # safe-looking direction. Found by Backend reading my code.
    #
    # The deeper half: reading an env var is only "live" if the process
    # RECEIVES it. This runs in the dbt container, which did not have
    # SEMANTIC_API_POOL_MAX at all, so reading it there would have looked live
    # and returned the default forever - the same bug wearing a different hat.
    # compose/insight.yml now passes it through, and the provenance
    # column is what makes a regression of either kind visible instead of
    # inferred.
    def _env_int(name: str, default: int) -> tuple[int, str]:
        raw = os.environ.get(name)
        if raw is None:
            return default, f"default {default} ({name} not set here)"
        return int(raw), f"env {name}"

    api_pool, api_src = _env_int("SEMANTIC_API_POOL_MAX", 16)
    dbt_threads, dbt_src = _env_int("DBT_THREADS", 4)
    consumers = [
        ("semantic-api pool", api_pool, api_src),
        ("dbt (threads + 1)", dbt_threads + 1, dbt_src),
        # Literal on purpose: three connections opened at fixed points in
        # Backend's runner (warehouse_conn, heartbeat, status_conn), none of
        # them configurable. Confirmed with Backend rather than assumed.
        ("CDC loader", 3, "literal - not configurable (runner.py 229/413/444)"),
        # CORRECTED. This said "literal - fixed in compose/insight.yml",
        # which cited evidence that does not support it: that file sets
        # --disable-default-metrics and a custom query path, both of which
        # change WHAT the exporter queries, not HOW MANY connections it opens.
        # Nothing pins this number. It is an allowance I chose, and labelling it
        # a structural constant was the same overstatement this column exists to
        # expose - in the column itself.
        #
        # It shares the warehouse_rls role with the semantic-api, so usename
        # cannot separate them - which is why an earlier `warehouse_rls = 2`
        # reading in this build was not attributable to either consumer. The
        # exporter now sets application_name=warehouse-exporter in its DSN
        # (compose/insight.yml), so the NEXT measurement can attribute
        # it. It is still UNVERIFIED because nobody has taken that measurement,
        # and the label changes when someone does, not when the means to do it
        # exists. To close it:
        #   SELECT application_name, count(*) FROM pg_stat_activity
        #    WHERE datname = 'warehouse' GROUP BY 1;
        ("postgres_exporter", 3, "UNVERIFIED allowance - measurable via application_name, not yet measured"),
        ("ad-hoc psql headroom", 4, "literal - policy allowance, not a setting"),
    ]
    with wh.cursor() as cur:
        cur.execute("SELECT current_setting('max_connections')::int, "
                    "current_setting('superuser_reserved_connections')::int")
        max_conn, reserved = cur.fetchone()
    usable = max_conn - reserved
    claimed = sum(n for _, n, _ in consumers)
    if claimed > usable:
        ok = False
        print(f"CONNECTION BUDGET OVERSUBSCRIBED: {claimed} claimed vs {usable} usable "
              f"(max_connections {max_conn} - {reserved} reserved)", file=sys.stderr)
        for label, n, src in consumers:
            print(f"    {n:>3}  {label:<22} [{src}]", file=sys.stderr)
        print("  Raise max_connections in analytics/warehouse/postgresql.conf, or lower a pool. "
              "Exhaustion surfaces as a 503 from semantic-api and a failed dbt thread, "
              "neither of which names this as the cause.", file=sys.stderr)
    else:
        print(f"OK  connection budget: {claimed} claimed of {usable} usable "
              f"({usable - claimed} spare)")
        for label, n, src in consumers:
            print(f"      {n:>3}  {label:<22} [{src}]")
    return 0 if ok else 5


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sync-policy", help="Materialise warehouse.column_policy from custom_pdp_core")

    p = sub.add_parser(
        "import-policy",
        help="Load warehouse.column_policy from CSV, for an Insight client that is not on Odoo",
    )
    p.add_argument("--file", required=True, help="CSV: source_table,source_column,pdp_class[,nullable]")

    p = sub.add_parser("gen-raw-ddl", help="Generate and apply raw.* landing tables from the policy")
    p.add_argument("--print-only", action="store_true", help="print the DDL instead of applying it")

    sub.add_parser("gen-fdw", help="Create the per-tenant foreign schema over the Odoo database")

    p = sub.add_parser("load-fixture", help="DEV ONLY: policy-driven masked snapshot load into raw.*")
    p.add_argument("--tenant", help="load only this tenant")

    p = sub.add_parser("tombstone", help="Append an _op='D' row to exercise delete semantics")
    p.add_argument("--table", required=True)
    p.add_argument("--id", required=True, type=int)
    p.add_argument("--tenant", required=True)

    sub.add_parser("verify", help="Re-check the classification and secret-exclusion invariants")

    args = ap.parse_args()
    return {
        "sync-policy": cmd_sync_policy,
        "import-policy": cmd_import_policy,
        "gen-raw-ddl": cmd_gen_raw_ddl,
        "gen-fdw": cmd_gen_fdw,
        "load-fixture": cmd_load_fixture,
        "tombstone": cmd_tombstone,
        "verify": cmd_verify,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
