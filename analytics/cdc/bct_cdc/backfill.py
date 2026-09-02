"""The initial snapshot: a separate code path from steady state, and resumable by construction.

Why separate at all (Backend brief, scope A): a snapshot reads whole tables with ``SELECT`` while
steady state reads a WAL stream. Sharing one code path between them means one of the two is being
emulated by the other, and the emulation is where the bugs live.

**How the two paths meet without losing a row.** Order matters and is enforced by
:mod:`bct_cdc.runner`:

1. The publication is created out of band, as ``odoo`` (``warehouse_reader`` cannot, by design).
2. The replication slot is created. From that instant Postgres retains WAL, so the slot's
   ``consistent_point`` is a floor: every change after it *will* be replayed.
3. The backfill runs, landing rows with ``_lsn`` = the snapshot LSN.
4. Streaming starts from the slot and replays everything since step 2.

A row modified *during* the backfill therefore lands twice: once from the snapshot at the snapshot
LSN, and once from the stream at a strictly higher LSN. Contract 05's mart rule — latest
non-deleted version per key, ordered by ``_lsn`` — makes the stream version win. A row deleted
during the backfill lands as a snapshot row plus a higher-LSN tombstone, and disappears from the
mart. That is why this is at-least-once rather than exactly-once, and why at-least-once suffices.

**Resumability, and why there is no progress table.** The resume point is
``max(id)`` already landed for this tenant, read back out of ``raw.<table>`` itself
(:func:`bct_cdc.warehouse.landed_high_water`). Paging is by keyset (``WHERE id > last_pk ORDER BY
id``), never ``OFFSET`` — an ``OFFSET`` pager re-scans everything before the cursor on every page,
which makes resuming *more* expensive than restarting, and that is how a nominally resumable
backfill becomes one nobody dares resume.

Deriving the resume point from the data rather than from a side table removes an entire class of
bug: a progress row committed separately from the rows it describes can end up ahead of them (rows
silently lost on restart) or behind them (rows duplicated). It also happens to be the only option
available, since ``warehouse_loader`` holds no ``CREATE`` — but it would be the right choice anyway.
"""

from __future__ import annotations

import logging
import time

from . import metrics as m
from . import source as src
from . import warehouse as wh
from .pgoutput import parse_lsn

_logger = logging.getLogger(__name__)


def backfill_table(
    source_conn,
    warehouse_conn,
    tenant: str,
    table: str,
    plan,
    snapshot_lsn: str,
    slot: str,
    batch_size: int = 2000,
    on_chunk=None,
) -> int:
    """Snapshot one table into ``raw.<table>``. Returns rows landed in *this* run."""
    last_pk, epoch_lsn = wh.landed_high_water(warehouse_conn, tenant, table)

    # Reuse the epoch already in the landing zone so every snapshot row for this tenant carries one
    # LSN. A second epoch would order some snapshot rows above others for no reason.
    if epoch_lsn:
        snapshot_lsn = epoch_lsn

    columns = plan.select_columns
    total = src.max_pk(source_conn, table)
    rows_done = 0
    landed = 0

    if last_pk:
        _logger.info(
            "resuming backfill of %s.%s from id > %d (of %d) at epoch %s",
            tenant, table, last_pk, total, snapshot_lsn,
        )

    while True:
        chunk = src.fetch_chunk(source_conn, table, columns, last_pk, batch_size)
        if not chunk:
            break
        now = wh.utcnow()
        rows = []
        for raw_row in chunk:
            masked = plan.apply(raw_row)
            rows.append(tuple(masked[c] for c in columns) + (now, "I", tenant, snapshot_lsn))
        chunk_last_pk = int(chunk[-1]["id"])

        with warehouse_conn:
            written = wh.insert_rows(warehouse_conn, table, columns, rows)
        landed += written
        rows_done += len(chunk)
        last_pk = chunk_last_pk

        m.ROWS_TOTAL.labels(tenant=tenant, source_table=table, op="I").inc(written)
        if total:
            m.BACKFILL_PROGRESS.labels(tenant=tenant, source_table=table).set(
                min(1.0, last_pk / float(total))
            )
        m.LAST_SUCCESS.labels(tenant=tenant, source_table=table).set(time.time())
        _logger.info(
            "backfill %s.%s: %d rows this run (id <= %d of %d)",
            tenant, table, rows_done, last_pk, total,
        )
        if on_chunk is not None:
            # Test hook: lets the resumability test stop at a known point.
            on_chunk(table, last_pk, rows_done)

    m.BACKFILL_PROGRESS.labels(tenant=tenant, source_table=table).set(1.0)
    wh.record_success(
        warehouse_conn, tenant, table,
        parse_lsn(snapshot_lsn) if snapshot_lsn else None, landed, slot,
    )
    _logger.info("backfill %s.%s complete: %d rows landed this run", tenant, table, landed)
    return landed
