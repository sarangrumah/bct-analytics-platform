"""The source side: introspection, publication checks, and replication-slot lifecycle.

The loader connects as ``warehouse_reader``, which holds only ``SELECT`` + ``REPLICATION``
(contract 04). There is no write path from the warehouse into Odoo (anti-pattern 7.10) -- not by
policy, but because the role cannot. Nothing in this module tries to work around that: the
publication is created out of band by ``scripts/analytics/cdc-provision.sh`` running as ``odoo``,
because ``CREATE PUBLICATION`` requires ownership and ``warehouse_reader`` correctly does not have it.
"""

from __future__ import annotations

import logging

import psycopg2
import psycopg2.extras
from psycopg2 import sql

from .pgoutput import parse_lsn

_logger = logging.getLogger(__name__)


class SlotInvalidated(RuntimeError):
    """The replication slot's ``wal_status`` is ``lost``.

    Security finding T-2, made loud rather than survivable. ``max_slot_wal_keep_size = 2GB`` is the
    accepted trade in ADR 0001: past the cap Postgres invalidates the slot to keep Odoo alive. The
    WAL those changes lived in is gone, so *reconnecting would produce a mart with a hole in it* and
    no error anywhere. The only correct response is to stop, alert, and re-snapshot.
    """


class PublicationMissing(RuntimeError):
    """The per-tenant publication does not exist. Run ``scripts/analytics/cdc-provision.sh``."""


def source_columns(conn, table: str) -> list:
    """Return ``[(column, data_type)]`` in ordinal order, from the source's own catalogue.

    Read from ``information_schema`` rather than from the policy on purpose: a column that exists in
    the database but is missing from ``warehouse.column_policy`` is exactly the case that must
    hard-fail, and comparing the policy to itself would never find it.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
            (table,),
        )
        rows = cur.fetchall()
    if not rows:
        raise RuntimeError("Source table public.%s does not exist" % table)
    return [(r[0], r[1]) for r in rows]


def publication_exists(conn, publication: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_publication WHERE pubname = %s", (publication,))
        return cur.fetchone() is not None


def publication_tables(conn, publication: str) -> dict:
    """Return ``{table: [columns]}`` as the publication actually declares them.

    Used to assert the structural ``secret`` control: if a secret column appears here, Postgres will
    put it on the wire and the loader must refuse to run rather than rely on filtering it later.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.relname, a.attname
            FROM pg_publication p
            JOIN pg_publication_rel pr ON pr.prpubid = p.oid
            JOIN pg_class c ON c.oid = pr.prrelid
            LEFT JOIN LATERAL unnest(pr.prattrs) AS pub_attnum ON true
            LEFT JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = pub_attnum
            WHERE p.pubname = %s
            """,
            (publication,),
        )
        out = {}
        for table, column in cur.fetchall():
            out.setdefault(table, [])
            if column is not None:
                out[table].append(column)
        return out


def slot_status(conn, slot: str) -> dict:
    """Return the server's view of the slot: existence, activity, wal_status and retained bytes."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT active,
                   wal_status,
                   COALESCE(pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn), 0)::bigint,
                   confirmed_flush_lsn::text
            FROM pg_replication_slots
            WHERE slot_name = %s
            """,
            (slot,),
        )
        row = cur.fetchone()
    if row is None:
        return {"exists": False, "active": False, "wal_status": None, "lag_bytes": 0, "lsn": None}
    return {
        "exists": True,
        "active": bool(row[0]),
        "wal_status": row[1],
        "lag_bytes": int(row[2]),
        "lsn": row[3],
    }


def assert_slot_healthy(conn, slot: str) -> dict:
    status = slot_status(conn, slot)
    if status["exists"] and status["wal_status"] == "lost":
        raise SlotInvalidated(
            "Replication slot %s has wal_status='lost'. The 2 GB max_slot_wal_keep_size cap fired "
            "and Postgres discarded the WAL this consumer had not read yet (ADR 0001, accepted "
            "trade: sacrifice the warehouse, protect the ERP). Reconnecting now would silently "
            "produce a mart with a hole in it. Drop the slot, re-provision, and re-run the "
            "backfill." % slot
        )
    return status


def ensure_slot(replication_conn, slot: str) -> str:
    """Create the logical slot if it is absent; return its ``consistent_point`` LSN as text.

    The publication must already exist -- contract 04 is explicit that a slot created before its
    consumer is ready is precisely the failure the 2 GB cap exists to bound, because WAL retention
    starts the instant the slot does.
    """
    cur = replication_conn.cursor()
    cur.execute(
        "SELECT confirmed_flush_lsn::text FROM pg_replication_slots WHERE slot_name = %s", (slot,)
    )
    row = cur.fetchone()
    if row is not None:
        _logger.info("replication slot %s already exists at %s", slot, row[0])
        return row[0]
    cur.execute(
        sql.SQL("CREATE_REPLICATION_SLOT {} LOGICAL pgoutput NOEXPORT_SNAPSHOT").format(
            sql.Identifier(slot)
        )
    )
    created = cur.fetchone()
    lsn = created[1]
    _logger.info("created replication slot %s at consistent point %s", slot, lsn)
    return lsn


def drop_slot(conn, slot: str) -> None:
    """Drop the slot so no WAL is retained. Only ever called on an explicit teardown."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_drop_replication_slot(%s) WHERE EXISTS ("
                    "SELECT 1 FROM pg_replication_slots WHERE slot_name = %s)", (slot, slot))


def current_wal_lsn(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_current_wal_lsn()::text")
        return parse_lsn(cur.fetchone()[0])


def max_pk(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT COALESCE(MAX(id), 0) FROM {}").format(sql.Identifier("public", table)))
        return int(cur.fetchone()[0])


def fetch_chunk(conn, table: str, columns: list, after_pk: int, limit: int) -> list:
    """One resumable backfill page, ordered by primary key.

    Keyset pagination rather than ``OFFSET``: an ``OFFSET`` scan re-reads everything before it on
    every page, so a table that fails at 80% costs more to resume than to restart -- which is how a
    "resumable" backfill quietly becomes one that nobody dares resume.
    """
    statement = sql.SQL("SELECT {} FROM {} WHERE id > %s ORDER BY id LIMIT %s").format(
        sql.SQL(", ").join(sql.Identifier(c) for c in columns),
        sql.Identifier("public", table),
    )
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(statement, (after_pk, limit))
        return [dict(r) for r in cur.fetchall()]
