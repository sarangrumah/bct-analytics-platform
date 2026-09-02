"""Prometheus instrumentation for the CDC consumer.

Names are the ones published in ``docs/agents/contracts/06-api.md`` and agreed with the Data
Warehouse agent through the Lead, because DWH owns the Grafana panels that read them. Renaming one
breaks a dashboard silently, so treat these as a contract, not as log lines.

There is deliberately no ``rows_per_second`` gauge. DWH derives throughput as
``rate(bct_cdc_rows_total[5m])`` and states the window in the panel legend, which keeps the
averaging window visible to whoever reads the panel instead of hiding it inside this process.

Note deliberately kept here rather than in a dashboard comment: this exporter is the *consumer's*
belief about its own lag. ``postgres_exporter`` separately publishes the *server's* view
(``pg_replication_slots_pg_wal_lsn_diff``). The two disagreeing is a stronger fault signal than
either number alone -- it catches a consumer that believes it is caught up while Postgres says it is
2 GB behind, which is exactly the state that ends with an invalidated slot.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, start_http_server

ROWS_TOTAL = Counter(
    "bct_cdc_rows_total",
    "Rows landed in the raw schema.",
    ["tenant", "source_table", "op"],
)

END_TO_END_LAG = Gauge(
    "bct_cdc_end_to_end_lag_seconds",
    "Seconds between the source transaction commit and the row landing in raw.",
    ["tenant", "source_table"],
)

SLOT_LAG_BYTES = Gauge(
    "bct_cdc_replication_slot_lag_bytes",
    "WAL bytes retained for this slot: pg_current_wal_lsn() - confirmed_flush_lsn.",
    ["tenant", "slot"],
)

SLOT_INVALIDATED = Gauge(
    "bct_cdc_slot_invalidated",
    "1 when the replication slot's wal_status is 'lost'. The 2 GB cap fired; a re-snapshot is "
    "required and the mart has a hole until it is done.",
    ["tenant", "slot"],
)

LAST_SUCCESS = Gauge(
    "bct_cdc_last_success_timestamp_seconds",
    "Unix timestamp of the last successful poll cycle for this table. This is a HEARTBEAT, not an "
    "event: it advances on every successful cycle including cycles that moved zero rows. If it only "
    "moved when rows flowed, an idle-but-healthy pipeline would be indistinguishable from a dead "
    "one -- and this metric backs meta.is_stale, so that mistake makes the dashboard lie about "
    "freshness in exactly the case where freshness matters.",
    ["tenant", "source_table"],
)

REDELIVERED_SKIPPED = Counter(
    "bct_cdc_redelivered_changes_skipped_total",
    "Changes dropped because their LSN was at or below the resume floor -- i.e. already landed "
    "before the last restart. Logical replication is at-least-once, so a NON-ZERO value here is "
    "normal after a restart and is the pipeline working, not failing. What matters is the shape: "
    "a step at restart and flat afterwards is healthy; continuous growth means the stream is "
    "looping over WAL it never confirms.",
    ["tenant", "source_table"],
)

FAILURES = Counter(
    "bct_cdc_failure_count_total",
    "Loader failures.",
    ["tenant", "source_table"],
)

BACKFILL_PROGRESS = Gauge(
    "bct_cdc_backfill_progress_ratio",
    "Resumable snapshot progress, 0..1. Stays where it was across a restart.",
    ["tenant", "source_table"],
)

UP = Gauge(
    "bct_cdc_up",
    "1 while the consumer holds its replication slot and is streaming.",
    ["tenant"],
)

def serve(port: int) -> None:
    start_http_server(port)


LANDING_AMPLIFICATION = Gauge(
    "bct_cdc_landing_row_amplification",
    "raw rows divided by distinct primary keys, per landing table. Append-only versioning means "
    "this is legitimately above 1 -- one row per change -- but a value climbing on a table nobody "
    "is bulk-editing is the signature of a backfill that re-ran as a NEW epoch and re-landed every "
    "row. Published because the only reason anyone noticed the last such episode is that QA went "
    "looking; a number on a dashboard is cheaper than an audit.",
    ["tenant", "source_table"],
)

LANDING_DUPLICATE_CHANGES = Gauge(
    "bct_cdc_landing_duplicate_changes",
    "Landing rows that share (id, _op, _lsn) with another row, per table: the same change landed "
    "twice. The known mechanism is at-least-once redelivery after a restart, since feedback "
    "follows durability by design; the resume floor now prevents new ones. Because a redelivery "
    "is the same WAL record, duplicate payloads are identical and the marts absorb them. This "
    "gauge does NOT self-clear, so a steady non-zero value is history and only GROWTH after a "
    "stable restart is a fault. Counted only among rows "
    "that HAVE an LSN -- SQL treats two NULL-bearing rows as equal for DISTINCT, so without that "
    "filter two genuinely different unordered changes are reported as one duplicate. That was a "
    "real false positive on sale_order_line, not a hypothetical.",
    ["tenant", "source_table"],
)

LANDING_UNORDERED = Gauge(
    "bct_cdc_landing_unordered_rows",
    "Landing rows with a NULL _lsn. These are NOT lost to the marts: DWH's raw_latest macro orders "
    "by coalesce(_lsn, '0/0'), so a NULL sorts last in precedence and any real CDC row supersedes "
    "it for the same key -- which is what makes a re-snapshot safe to run over live data. What a "
    "NULL does cost is a TOTAL order: (_tenant_id, pk, _lsn) stops being unique, so two distinct "
    "changes can share a key. This loader never writes one (every row carries format_lsn of a real "
    "WAL position), so a non-zero value means rows arrived by another route -- historically DWH's "
    "warehouse_ctl.py load-fixture, which is moving to an explicit '0/0'.",
    ["tenant", "source_table"],
)
