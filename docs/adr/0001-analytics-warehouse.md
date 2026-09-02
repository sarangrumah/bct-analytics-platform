# ADR 0001 — Analytics warehouse: engine, extraction and freshness

- **Status**: **Accepted at GATE 2 on 2026-08-31 by the operator**
- **Date**: 2026-08-31
- **Deciders**: Operator (engine choice), Lead (design), Security (veto)
- **Supersedes**: none

## Context

The platform has no data warehouse. Reporting would otherwise read Odoo's OLTP Postgres directly,
which couples dashboard load to transactional performance and offers no historical modelling.

Master prompt §2 sets a hard requirement that reframes the whole decision: **the mart is wired
directly to Odoo's own Postgres and stays current.** This is explicitly not a nightly export
(anti-pattern §7.7). Whatever we choose must sustain continuous, low-latency ingestion from a live
Odoo 19 Postgres 16 instance.

### Constraints, measured rather than assumed

Target deployment is a **single Biznet Gio VPS (Ubuntu 24.04)** already carrying the full application
stack plus Prometheus/Grafana/Loki. To size honestly, the Lead measured a directly comparable running
stack on this host (`docker stats --no-stream`, 2026-08-31):

| Component | Measured RSS |
|---|---|
| odoo | 452 MiB |
| postgres (OLTP) | 538 MiB |
| redis | 9 MiB |
| prometheus | 46 MiB |
| grafana | 92 MiB |
| loki | 89 MiB |
| promtail | 51 MiB |
| alertmanager | 15 MiB |
| **Subtotal already committed** | **≈ 1.29 GiB** |

A second Postgres acting as a warehouse measured **66 MiB idle** on the same host. On a common
8 GiB VPS that leaves roughly 6.7 GiB of headroom before the warehouse, the semantic API, the login
gateway and the Next.js dashboard are added.

Image size is **not** a differentiator and should not be used as one — measured compressed amd64
layers: `clickhouse/clickhouse-server:24-alpine` **143 MB** vs `postgres:16-alpine` **111 MB**.

## Options considered

### Option A — Postgres-native marts

A second Postgres 16 database (`warehouse`), fed by native logical replication from the Odoo
Postgres, transformed with dbt-core (`dbt-postgres`).

### Option B — Columnar OLAP

ClickHouse (or DuckDB single-node) as the analytical store, fed by CDC, transformed with
`dbt-clickhouse`.

## Comparison against the real constraints

| Dimension | Option A — Postgres | Option B — ClickHouse |
|---|---|---|
| **RAM at rest** | 66 MiB measured; ~512 MiB–1 GiB budgeted under load | ClickHouse sizes caches from host RAM and expects tuning; the practical floor for a stable server is ~2 GiB, and default `max_server_memory_usage` is a share of *host* RAM, which on a shared VPS competes with Odoo |
| **Disk** | Row store + indexes; larger than columnar, but at our volumes the absolute number is small | Better compression; the win only materialises at volumes we do not have |
| **CDC path from Postgres** | **Native.** `pgoutput` logical decoding → publication/slot per tenant → a small Python consumer. No extra moving parts. | Either Debezium + Kafka (**anti-pattern §7.6** — a heavyweight orchestrator we cannot justify on this VPS), or ClickHouse's `MaterializedPostgreSQL` engine, which upstream still documents as **experimental** and which we would be betting tenant-isolation correctness on |
| **SCD Type 2** (required by §3.1 for `dim_partner`, `dim_product`) | `dbt snapshot` is first-class on `dbt-postgres`; merge/update is native | ClickHouse has no efficient `UPDATE`; SCD2 needs `ReplacingMergeTree` plus `FINAL` semantics and careful handling. dbt-clickhouse snapshot support is materially weaker. **This alone is close to decisive.** |
| **Tenant isolation at the storage layer** (required by §3.3, not application-level filtering) | **Native Row-Level Security.** A tenant-scoped role provably cannot read another tenant's rows, enforced by the engine | Row policies exist but are less mature and less idiomatic to enforce per-connection; the common answer is database-per-tenant, which multiplies ops |
| **Backup** | Reuses the `pg_dump`/filestore conventions of `scripts/tenant-backup.sh` — one tool, one runbook | Needs `clickhouse-backup`, a separate tool with its own failure modes and its own runbook |
| **Monitoring** | `postgres_exporter` is already in the observability overlay; slot lag is a standard exported metric | Needs a ClickHouse exporter and new dashboards; slot lag still has to be read from Postgres anyway |
| **Team skill** | Codebase is Python + Postgres. Zero new engine to learn. | New query dialect, new operational model, new failure modes, on a team with no stated ClickHouse experience |
| **dbt adapter maturity** | `dbt-postgres` is core-maintained | `dbt-clickhouse` is vendor-maintained, fewer features |

### When Option B would actually win

Columnar storage earns its operational cost somewhere above ~10⁸ fact rows or when scans routinely
touch hundreds of millions of rows. Our largest fact will be `fct_account_move_line`. For a mid-size
Odoo tenant that is on the order of **10⁵–10⁶ rows per year**. Postgres with correct indexing and
pre-aggregated marts answers that in milliseconds. We would be paying ClickHouse's operational and
correctness cost for a scale problem we do not have, and cannot honestly project having.

## Decision

**Option A — Postgres-native marts, fed by native logical replication, transformed by dbt-core.**

Revisit if any single tenant's largest fact table passes 10⁸ rows, or if p95 mart query latency
exceeds the §4 budget after indexing and pre-aggregation are exhausted. Record that revisit as a new
ADR; do not migrate silently.

## Extraction design

### Method: native logical replication (`pgoutput`), not a `write_date` tap, not Debezium

Odoo carries `write_date` on nearly every table, which makes an incremental tap tempting. **It is
wrong here, and the master prompt is right about why.** An incremental-by-`write_date` tap silently
misses:

- hard deletes via `unlink()`,
- `ON DELETE CASCADE` on relation tables,
- direct SQL writes that bypass the ORM.

None of those leave a `write_date` trace, so the warehouse drifts and nothing reports it. Logical
decoding sees every `INSERT`, `UPDATE` **and `DELETE`** at the WAL level, which is exactly the gap.

Debezium is rejected under anti-pattern §7.6: it requires Kafka to be useful, and we cannot justify
that footprint next to Odoo on one VPS.

### Deletes

Every decoded `DELETE` lands in the `raw_` schema as a **tombstone**: the row is appended with
`_op = 'D'` and `_ingested_at`. The landing zone stays append-only. Marts filter to the latest
non-deleted version per key, so a delete in Odoo removes the row from the mart within the freshness
SLA. This is directly testable and §6 requires the test.

### Cost to the source — and why greenfield removes the usual risk

Logical replication requires `wal_level = logical`, which normally means changing the postgres service
command and **restarting it** — a change to shared infrastructure that the master prompt rightly
insists be treated as its own gated change.

**Because the operator chose a greenfield build, this cost is avoided entirely.** `wal_level=logical`
is set in `postgres/postgresql.conf` at first boot (Platform-Infra brief, scope item 3), so there is
never a restart of a running Odoo to enable CDC. This is a genuine and unplanned benefit of the
greenfield decision and is recorded here so it is not later mistaken for an unmanaged change.

### Replication slot safety — designed before the pipeline, not after

An inactive slot makes Postgres retain WAL indefinitely, which can fill the disk and **take Odoo
down**. A warehouse outage must never become an Odoo outage (anti-pattern §7.9). Mandatory, all three:

1. **`max_slot_wal_keep_size = 2GB`** set at first boot. Postgres drops a slot that exceeds it,
   sacrificing the warehouse rather than the ERP. That is the correct trade and it is deliberate.
2. **Slot lag exported to Prometheus** from `pg_replication_slots` via `postgres_exporter`.
3. **Alertmanager rule firing well before disk pressure** — warn at 512 MiB retained, critical at
   1 GiB, i.e. with 50% of the cap still in hand.

### Read-only by construction

The pipeline connects to Odoo as `warehouse_reader`, holding **only `SELECT` + `REPLICATION`**. There
is no write path from the warehouse into Odoo (anti-pattern §7.10) — not by policy, but because the
role cannot. Platform-Infra must prove this with a pasted permission denial.

### Multi-tenant

**One publication and one replication slot per tenant database.** Onboarding is automatic, by
extending the existing `scripts/tenant-provision.sh` rather than by hand: provisioning a tenant
creates its database, its publication, its slot, and its `dim_tenant` row in the same run. Every fact
and dimension carries `tenant_id`, and isolation is enforced by **Postgres RLS**, not by application
queries alone.

## Freshness targets per mart

Stated as numbers, with a defined consequence when missed. Not every mart deserves the same SLA —
over-engineering the tolerant ones costs headroom the strict ones need.

| Mart | Freshness SLA | On breach |
|---|---|---|
| `mart_ppob_transaction` | **60 s** | Page: PPOB is operational, SLA breaches are the point of the view |
| `mart_stock_position` | **5 min** | Alert; dashboard shows `is_stale` |
| `mart_sales_daily` | **5 min** | Alert; dashboard shows `is_stale` |
| `mart_revenue_daily` | **15 min** | Alert |
| `mart_account_move_line` / finance | **60 min** | Alert; financial reporting tolerates hourly |
| `dim_*` (SCD2) | **60 min** | Alert |

`meta.last_refreshed_at` and `meta.is_stale` are served from `warehouse.pipeline_state` per metric
contract §3 — read from real pipeline metadata, never from a client clock.

## Scheduler

**Reuse Odoo `ir.cron` and a systemd-style container loop before adding anything.** The CDC consumer
is a long-running process, not a scheduled job, so the only scheduled work is dbt (`dbt build`) on a
short interval. That does not justify Dagster or Airflow on this VPS. If mart dependencies later grow
a DAG complex enough that a plain interval is unsafe, that is a new ADR with its own footprint budget.

## Consequences

**Positive.** One engine to operate, back up, patch and monitor. Native CDC with correct delete
semantics. RLS gives storage-layer tenant isolation that is provable by test. dbt snapshots give
real SCD2. No new language or operational model for the team. Comfortably inside the VPS budget.

**Negative.** Postgres will not match columnar scan performance if volumes grow by two orders of
magnitude; we accept that and name the trigger to revisit. Row storage uses more disk than columnar.
A capped slot means the warehouse can be dropped under sustained lag — deliberate, but it means slot
lag alerting is load-bearing, not decorative.

**Risks accepted.** Logical decoding adds WAL volume on the source. Odoo schema changes on module
upgrade can break replication of a changed table; dbt source freshness plus reconciliation tests must
catch it loudly (§3.4) rather than letting the mart drift quietly.

## GATE 2 resolution (2026-08-31)

The operator approved all three decision points as proposed:

1. **Engine: Option A** — Postgres-native marts, native logical replication, dbt-postgres.
2. **Freshness: the table above is accepted as written**, including the deliberate non-uniformity
   (PPOB 60 s, finance 60 min). Uniform-strict was explicitly rejected as wasting VPS headroom.
3. **`max_slot_wal_keep_size = 2GB` confirmed**, with the trade understood and accepted: under
   sustained lag Postgres drops the warehouse slot, killing the pipeline in order to keep Odoo
   alive. The pipeline is re-seedable from snapshot; the ERP is not expendable. Alerting at
   512 MiB (warn) and 1 GiB (critical) is therefore load-bearing, not decorative.

This ADR is now binding on the Data Warehouse and Backend agents.
