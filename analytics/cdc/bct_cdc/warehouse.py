"""Everything the loader does to the warehouse database: assertions, writes, and pipeline state.

Contract 05 governs every name here, and section A of it governs what this code may *do*. The
loader connects as ``warehouse_loader``, which holds:

* ``SELECT`` + ``INSERT`` on ``raw.*`` — **no ``UPDATE``, no ``DELETE``**, so the append-only rule
  is enforced by the grant rather than trusted to this module's discipline;
* full DML on ``warehouse.pipeline_state``;
* ``SELECT`` on ``warehouse.column_policy`` and ``warehouse.tenant_registry``;
* **no ``CREATE`` anywhere.**

That last one is the important one. DWH generates the ``raw.*`` DDL from ``warehouse.column_policy``
(``make warehouse-raw-ddl``), and a loader that could create its own landing table could land a
column with no policy row — which would turn "unclassified is a hard failure" from a structural fact
back into a convention this code has to remember. So a missing table is a schema-drift signal to
report, never something to fix in flight.
"""

from __future__ import annotations

import datetime as dt
import logging

import psycopg2
import psycopg2.extras
from psycopg2 import sql

from .pgoutput import format_lsn, parse_lsn
from .policy import ColumnPolicy

#: Contract 05 SA.6 -- the value DWH published for this consumer. Do not invent a variant.
APPLICATION_NAME = "cdc-loader"

_logger = logging.getLogger(__name__)

#: The four bookkeeping columns of contract 05.
META_COLUMNS = (
    ("_ingested_at", "timestamptz"),
    ("_op", "char(1)"),
    ("_tenant_id", "text"),
    ("_lsn", "pg_lsn"),
)


# Hand back json/jsonb as the raw text Postgres sent, rather than letting psycopg2 parse it into a
# dict. Two reasons, both about fidelity of the landing zone:
#   * a parsed dict cannot be re-adapted on insert without a Json() wrapper, and wrapping it would
#     re-serialise with Python's key order -- so the bytes in the warehouse would differ from the
#     bytes in Odoo for no reason;
#   * the pgoutput stream delivers every value as text, so keeping the backfill on text too means
#     both code paths land byte-identical values. A row that differed between snapshot and stream
#     would show up as a spurious "change" in the mart forever.
# Odoo 19 uses jsonb heavily (49 of the 724 columns in scope) for translated and company-dependent
# fields, so this is not an edge case.
psycopg2.extras.register_default_json(loads=lambda value: value)
psycopg2.extras.register_default_jsonb(loads=lambda value: value)


def connect(dsn: str, autocommit: bool = False):
    """Open a connection that NAMES ITSELF, per contract 05 SA.6.

    ``application_name`` is not decoration. ``warehouse.access_audit.application_name`` reads
    ``current_setting('application_name')``, and ``log_line_prefix``'s ``%a`` is the fallback that
    keeps a warehouse read attributable when nothing calls ``log_access()``. Unset, an audit row
    records NULL for the one column saying WHICH service read the data.

    Applied to every connection this helper opens, warehouse and Odoo-source alike: naming the
    source connections costs nothing and makes the loader visible in Odoo's ``pg_stat_activity``
    too.

    The ONE connection it does not cover is the logical-replication connection in ``runner.py``,
    which psycopg2 opens directly with a ``connection_factory``. That is **out of scope by
    construction, not pending**: it is a source-side connection to the Odoo Postgres, and contract
    05 SA.6 governs *warehouse* consumers. Naming it would help attribute WAL-sender sessions for
    slot monitoring, but that is contract 04's concern -- if it is ever wanted it arrives as a
    Platform-Infra request, not as a tidy-up here.

    This paragraph previously said the connection was "left alone rather than changed
    speculatively while QA holds the stack", which was true and read as a deferral -- an invitation
    to do it once the stack was free, which is precisely what DWH then asked nobody to do. A
    comment that describes a timing constraint where the real reason is scope becomes a TODO the
    moment the timing passes.
    """
    conn = psycopg2.connect(dsn, application_name=APPLICATION_NAME)
    conn.autocommit = autocommit
    return conn


class SchemaDrift(RuntimeError):
    """A table the loader must write does not exist, or lacks a column the plan would land.

    Fatal, and deliberately not self-healing — see this module's docstring.
    """


class ColumnPolicyMissing(RuntimeError):
    """The contract-05 warehouse tables are not usable by this role.

    The message disambiguates a failure mode contract 05 section A.5 calls out explicitly: a role
    with no privilege on a schema sees its tables as **absent**, not as inaccessible. An empty
    ``information_schema`` is therefore ambiguous between "the DDL never ran" and "this role cannot
    see it", and the two have completely different fixes.
    """


# ----------------------------------------------------------------------------------------------
# Assertions -- the loader creates nothing
# ----------------------------------------------------------------------------------------------


def assert_pipeline_tables(conn) -> None:
    """Check the contract-05 tables exist and carry the privilege the loader needs."""
    required = (("warehouse.column_policy", "SELECT"), ("warehouse.pipeline_state", "INSERT"))
    missing = []
    with conn.cursor() as cur:
        for table, privilege in required:
            cur.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))
            if not cur.fetchone()[0]:
                missing.append("%s (absent, or invisible to this role)" % table)
                continue
            cur.execute("SELECT has_table_privilege(%s, %s)", (table, privilege))
            if not cur.fetchone()[0]:
                missing.append("%s (visible but no %s)" % (table, privilege))
    if missing:
        raise ColumnPolicyMissing(
            "Cannot use the contract-05 warehouse tables: %s. They are produced by the Data "
            "Warehouse agent; the CDC loader reads them and never creates them. A role with no "
            "privilege on a schema sees its tables as ABSENT rather than inaccessible, so check "
            "the role's grants before concluding the DDL never ran." % ", ".join(missing)
        )


def policy_tables(conn) -> list:
    """The source tables DWH has classified. This is the loader's table list.

    Derived rather than hardcoded, so the seam stays where contract 05 puts it: DWH decides what is
    replicated by classifying it, and the loader follows. A hardcoded list here would drift the
    first time DWH added or removed a table — as it already has, since ``res_users`` is classified
    in Odoo but deliberately absent from the warehouse policy.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT source_table FROM warehouse.column_policy ORDER BY 1")
        return [r[0] for r in cur.fetchall()]


def landing_columns(conn, table: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'raw' AND table_name = %s",
            (table,),
        )
        rows = cur.fetchall()
    if not rows:
        raise SchemaDrift(
            "raw.%s does not exist, or warehouse_loader cannot see it. DWH generates the landing "
            "DDL from warehouse.column_policy with `make warehouse-raw-ddl`; the loader holds no "
            "CREATE on schema raw by design. Report this as schema drift rather than creating the "
            "table." % table
        )
    return {r[0]: r[1] for r in rows}


def assert_landing_table(conn, table: str, plan_columns) -> None:
    """Every column the plan would write must already exist in ``raw.<table>``."""
    actual = landing_columns(conn, table)
    for meta, _type in META_COLUMNS:
        if meta not in actual:
            raise SchemaDrift(
                "raw.%s is missing the contract-05 bookkeeping column %s." % (table, meta)
            )
    missing = sorted(c for c in plan_columns if c not in actual)
    if missing:
        raise SchemaDrift(
            "raw.%s is missing %d column(s) the masking plan would write: %s. The landing DDL and "
            "warehouse.column_policy have drifted apart; DWH regenerates it with "
            "`make warehouse-raw-ddl`." % (table, len(missing), ", ".join(missing))
        )


def landed_high_water(conn, tenant: str, table: str) -> tuple:
    """Return ``(max_landed_pk, epoch_lsn)`` for the snapshot already in the landing zone.

    **The resume point is derived from the data, not from a side table.** Two reasons, and the
    second only became visible once DWH published the real grants:

    * A separate progress table can disagree with the rows it describes — a crash between two
      commits leaves state ahead of the data (rows silently lost) or behind it (rows duplicated).
      Reading ``max(id)`` back out of the landing zone cannot disagree with itself, because it *is*
      the data.
    * ``warehouse_loader`` holds no ``CREATE``, so there is nowhere to put a side table anyway, and
      asking DWH for one would have added a moving part to save a single ``SELECT``.

    ``epoch_lsn`` is the lowest LSN this tenant has landed for the table, which is the snapshot's
    own LSN: the backfill runs before streaming, so its rows are always the oldest.
    """
    ident = sql.Identifier("raw", table)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT COALESCE(MAX(id), 0), MIN(_lsn)::text FROM {} "
                "WHERE _tenant_id = %s AND _op = 'I'"
            ).format(ident),
            (tenant,),
        )
        row = cur.fetchone()
    return int(row[0] or 0), row[1]


def landed_max_lsn(conn, tenant: str, tables) -> str:
    """Highest ``_lsn`` this tenant has already landed, across every replicated table.

    This is the **resume floor** for the stream, and it exists because logical replication is
    at-least-once by construction, not by accident. :meth:`stream.StreamConsumer.flush` writes the
    warehouse transaction and only then calls ``send_feedback`` -- deliberately, because confirming
    an LSN the warehouse has not stored tells Postgres it may discard that WAL and the rows are
    then gone from both ends. The unavoidable cost of that ordering is a window: die between the
    commit and the feedback and Postgres redelivers changes that ARE already landed.

    That window is not hypothetical. DWH measured it on ``res_partner``::

        id | _op |   _lsn    | copies | first_seen              | last_seen
        46 | U   | 0/A313AC0 |   2    | 2026-08-31 08:13:54.86  | 2026-08-31 08:15:35.59
        47 | U   | 0/A3139D8 |   2    | 2026-08-31 08:13:54.86  | 2026-08-31 08:15:35.59

    Real LSNs, identical payloads, 101 seconds apart -- a restart resuming from a
    ``confirmed_flush_lsn`` that had not advanced past them.

    Derived from the DATA, not from ``warehouse.pipeline_state``, for the same reason
    :func:`landed_high_water` is: a progress table can disagree with the rows it describes, and
    reading the maximum back out of the landing zone cannot disagree with itself, because it *is*
    the data. The global maximum across tables is the correct floor rather than a per-table one:
    ``flush`` writes every buffered table inside ONE warehouse transaction, so landing is atomic
    across tables, and logical decoding delivers transactions in commit order. Any change at or
    below this LSN therefore belongs to a transaction that was already landed in full.

    Returns ``'0/0'`` when nothing has been landed yet, which floors nothing.
    """
    highest = 0
    for table in tables:
        ident = sql.Identifier("raw", table)
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT MAX(_lsn)::text FROM {} WHERE _tenant_id = %s").format(ident),
                (tenant,),
            )
            row = cur.fetchone()
        if row and row[0]:
            highest = max(highest, parse_lsn(row[0]))
    return format_lsn(highest)


def load_column_policy(conn) -> list:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source_table, source_column, pdp_class, transform, mask_null "
            "FROM warehouse.column_policy"
        )
        return [
            ColumnPolicy(
                source_table=r[0],
                source_column=r[1],
                pdp_class=r[2],
                transform=r[3],
                mask_null=bool(r[4]),
            )
            for r in cur.fetchall()
        ]


# ----------------------------------------------------------------------------------------------
# Pipeline state -- contract 05, and the only source of meta.last_refreshed_at
# ----------------------------------------------------------------------------------------------


def record_success(conn, tenant: str, table: str, lsn, rows: int, slot: str) -> None:
    """Advance ``warehouse.pipeline_state``. This is what ``meta.last_refreshed_at`` reads."""
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO warehouse.pipeline_state
                (tenant_id, source_table, last_lsn, last_success_at, rows_loaded, last_error,
                 failure_count, slot_name)
            VALUES (%s, %s, %s, now(), %s, NULL, 0, %s)
            ON CONFLICT (tenant_id, source_table) DO UPDATE SET
                last_lsn        = COALESCE(EXCLUDED.last_lsn, warehouse.pipeline_state.last_lsn),
                last_success_at = EXCLUDED.last_success_at,
                rows_loaded     = warehouse.pipeline_state.rows_loaded + EXCLUDED.rows_loaded,
                last_error      = NULL,
                failure_count   = 0,
                slot_name       = EXCLUDED.slot_name
            """,
            (tenant, table, format_lsn(lsn) if lsn else None, rows, slot),
        )


def record_failure(conn, tenant: str, table: str, error: str, slot: str) -> None:
    """Record a failure without clearing the last success — a stale mart must still say *when*."""
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO warehouse.pipeline_state
                (tenant_id, source_table, last_error, failure_count, slot_name)
            VALUES (%s, %s, %s, 1, %s)
            ON CONFLICT (tenant_id, source_table) DO UPDATE SET
                last_error    = EXCLUDED.last_error,
                failure_count = warehouse.pipeline_state.failure_count + 1,
                slot_name     = EXCLUDED.slot_name
            """,
            (tenant, table, error[:2000], slot),
        )


def heartbeat(conn, tenant: str, tables) -> None:
    """Advance ``last_success_at`` on a cycle that moved no rows.

    A heartbeat, not an event. An idle pipeline and a dead one are indistinguishable if this only
    moves when rows flow — and it backs ``meta.is_stale``, so that confusion makes the dashboard
    claim fresh data is stale, or worse, the reverse.
    """
    with conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE warehouse.pipeline_state SET last_success_at = now() "
            "WHERE tenant_id = %s AND source_table = ANY(%s)",
            (tenant, list(tables)),
        )


def insert_rows(conn, table: str, columns: list, rows: list) -> int:
    """Append masked rows to ``raw.<table>``.

    Plain ``INSERT``, with no ``ON CONFLICT``: DWH's landing tables carry ``(_tenant_id, id, _lsn)``
    as a **non-unique** index, so there is no constraint for a conflict clause to key on.
    Idempotency rests on the caller, and it is worth being precise about WHICH caller, because
    the earlier version of this docstring was true of one path and false of the other:

    * The **backfill** never re-reads a range — it resumes from the highest id already landed,
      so a replay finds nothing to insert. That has always held.
    * The **stream** had no such property. Feedback deliberately follows durability, so a death
      between the warehouse commit and ``send_feedback`` makes Postgres redeliver changes that
      are already landed. DWH measured exactly that on ``res_partner``: two changes, real LSNs,
      identical payloads, landed 101 seconds apart. The old sentence covered the backfill and
      silently implied the stream, which is how the duplicate went unexplained long enough for
      another agent to go looking for a fixture artefact in their own code.

    The stream now floors itself at :func:`landed_max_lsn`. A unique index on
    ``(_tenant_id, id, _op, _lsn)`` would let the database enforce this as well as the loader;
    it has been requested from DWH and does not exist yet, so the property still rests on the
    loader rather than on the storage layer. Stated plainly rather than implied.
    """
    if not rows:
        return 0
    ident = sql.Identifier("raw", table)
    all_columns = list(columns) + [name for name, _type in META_COLUMNS]
    statement = sql.SQL("INSERT INTO {} ({}) VALUES %s").format(
        ident, sql.SQL(", ").join(sql.Identifier(c) for c in all_columns)
    )
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, statement.as_string(cur), rows, page_size=1000)
        return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else len(rows)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def landing_amplification(conn, tenant: str, table: str) -> tuple:
    """Return ``(rows, distinct_ids, duplicate_change_rows, unordered_rows)`` for one table.

    Two different numbers, because they mean different things and conflating them is what made the
    last investigation slow:

    * ``rows / distinct_ids`` is **amplification**. Append-only versioning makes it legitimately
      greater than 1 -- an insert plus three computed-field updates is four rows for one id -- and
      the deliberate backfill/stream overlap adds one more. It is a trend to watch, not a fault.
    * ``duplicate_change_rows`` counts rows sharing ``(id, _op, _lsn)``, **among rows that have an
      LSN**. This is the number that distinguishes "the table grew because the data changed"
      from "the loader landed the same change twice". It used to say the latter had *no
      legitimate cause*. That was wrong, and the wrongness had a cost: DWH spent an
      investigation looking for a fixture artefact in their own code before establishing it
      was at-least-once redelivery from this loader. The cause is known — see
      :func:`landed_max_lsn` — the resume floor now prevents new occurrences, and because
      rows already landed are never removed, this figure does not return to 0 on its own.
      Read GROWTH, not level.
    * ``unordered_rows`` counts rows with a NULL ``_lsn``. They are **not** lost to the marts —
      DWH's ``raw_latest`` macro orders by ``coalesce(_lsn, '0/0')``, so a NULL sorts last in
      precedence and any real CDC row supersedes it for the same key, which is exactly what makes a
      re-snapshot safe over live data. What a NULL costs is a *total* order: ``(_tenant_id, pk,
      _lsn)`` stops being unique, so two distinct changes can share a key.

    The ``_lsn IS NOT NULL`` filter is not tidiness, it is a correctness fix for this metric. SQL
    row comparison treats two NULL-bearing rows as equal for ``DISTINCT``, so two genuinely
    different changes that both landed without an LSN counted as one duplicate. The first version
    of this query reported exactly that false positive on ``sale_order_line``.

    A NULL ``_lsn`` is its own signal and is now surfaced as one: contract 05 makes
    ``(_tenant_id, pk, _lsn)`` the ordering key, so a row without an LSN cannot participate in the
    mart's "latest non-deleted version per key" rule. This loader never writes one -- every landed
    row carries ``format_lsn`` of a real WAL position -- so a non-zero value here means rows arrived
    in the landing zone by some other route.
    """
    ident = sql.Identifier("raw", table)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT count(*), "
                "       count(DISTINCT id), "
                "       count(*) FILTER (WHERE _lsn IS NOT NULL) "
                "         - count(DISTINCT (id, _op, _lsn)) FILTER (WHERE _lsn IS NOT NULL), "
                "       count(*) FILTER (WHERE _lsn IS NULL) "
                "FROM {} WHERE _tenant_id = %s"
            ).format(ident),
            (tenant,),
        )
        row = cur.fetchone()
    return int(row[0]), int(row[1]), int(row[2]), int(row[3])
