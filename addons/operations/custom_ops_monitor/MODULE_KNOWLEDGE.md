---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_ops_monitor
manifest_version: 19.0.0.1.0
---

# custom_ops_monitor

## Purpose
Server-side ops dashboard that turns Prometheus, the `custom-predictor` ML service, and Alertmanager webhooks into Odoo records so the platform ops team has a single pane of glass per tenant — without leaving Odoo. Three pillars: minute-by-minute **tenant health snapshots** (`custom.ops.tenant.health`), hourly **capacity forecasts** (`custom.ops.capacity.forecast`), and webhook-ingested **incidents** (`custom.ops.incident`) that auto-create `mail.activity` for on-call.

## Business Flow
- **Health snapshots**: `_cron_collect_snapshots` (60s) iterates `tenant.registry` `state=active`, runs a fixed PromQL set via `PrometheusClient.values_by_label(result, "db")`, creates one `custom.ops.tenant.health` row per tenant tagged with `snapshot_at=now()`. Computed `health_score` (0-100) penalizes CPU>50, mem>60, disk>70, error_rate, stale/failed backups; computed `status` thresholds: ≥75 green, ≥50 yellow, <50 red. Backup freshness: ≤26h ok, ≤36h stale, otherwise failed.
- **Capacity forecasts**: `_cron_regenerate` (hourly) reads the last 30 days of `custom.ops.tenant.health` per tenant per metric (cpu/memory/disk/db_size), POSTs `{metric, history}` to the predictor URL (`custom_ops_monitor.predictor_url`, default `http://predictor:8000/forecast`), stores `forecast_30d/90d/365d` + confidence interval + `recommended_action` per row. `_compute_severity` flags `critical` when `forecast_30d > ceiling*0.9`, `warn` when `forecast_90d > ceiling*0.8` (ceilings: cpu/memory/disk = 100; db_size = None → always info).
- **Incidents**: Alertmanager POSTs to `/api/ops/alert` (HMAC-secured via `@secure_endpoint('ops_alertmanager')`). Controller calls `custom.ops.incident.ingest_alertmanager_payload(payload)` which iterates `payload["alerts"]` and upserts one row per alert keyed by `fingerprint`. New firing → `_schedule_ack_activity()` assigns a `mail.activity` ("Acknowledge: ...") to the first user in `custom_ops_monitor.group_ops_engineer`. Resolved alerts on existing rows → state=resolved + `resolved_at`; resolved status on unknown fingerprint is dropped (no row created).
- **Dashboard**: OWL component (`web.assets_backend`) renders the per-tenant heatmap tile, drills into time-series, and embeds a Grafana iframe at the URL set in `custom_super_admin.grafana_base_url`.

## Key Models
- `custom.ops.tenant.health` — Per-minute snapshot of CPU/memory/disk/error rate/backup freshness per tenant.
- `custom.ops.capacity.forecast` — Forecasts from the predictor service per (tenant, metric).
- `custom.ops.incident` — Alertmanager-driven incident record. Inherits `mail.thread`, `mail.activity.mixin`. `(fingerprint)` unique for upsert dedup.
- `PrometheusClient` — Plain Python helper (not a Model); thin urllib wrapper around `/api/v1/query` and `/api/v1/query_range`. Instantiated on demand.

## Important Fields
- `custom.ops.tenant.health.tenant_id` (M2o tenant.registry, cascade) — owner.
- `custom.ops.tenant.health.snapshot_at` (Datetime, indexed) — series timestamp.
- `custom.ops.tenant.health.cpu_pct` / `memory_pct` / `disk_pct` / `error_rate_pct` (Float) — raw metric values.
- `custom.ops.tenant.health.memory_mb_used/total` / `disk_gb_used/total` / `db_size_mb` (Integer) — absolute volumes.
- `custom.ops.tenant.health.request_rate_per_min` / `redis_hit_rate_pct` (Float) — throughput + cache health.
- `custom.ops.tenant.health.last_backup_at` (Datetime, copied from tenant) + `backup_status` (Selection ok/stale/failed, classified by `_classify_backup`) — backup freshness.
- `custom.ops.tenant.health.health_score` (Integer, computed, stored) — 0-100.
- `custom.ops.tenant.health.status` (Selection green/yellow/red, computed, stored, indexed) — RAG bucket.
- `custom.ops.capacity.forecast.metric` (Selection cpu/memory/disk/db_size, indexed) — forecast subject.
- `custom.ops.capacity.forecast.forecast_30d` / `forecast_90d` / `forecast_365d` (Float) — projections.
- `custom.ops.capacity.forecast.confidence_lower` / `confidence_upper` (Float) — predictor's CI.
- `custom.ops.capacity.forecast.recommended_action` (Char) — predictor-supplied prose.
- `custom.ops.capacity.forecast.severity` (Selection info/warn/critical, computed, stored) — derived from `forecast_*` vs `_CAPACITY_CEILING`.
- `custom.ops.incident.alert_name` / `severity` (info/warning/critical/page) / `fired_at` / `resolved_at` / `summary` / `description` / `runbook_url` — alert payload mirror.
- `custom.ops.incident.fingerprint` (Char, indexed, unique) — Alertmanager dedup key.
- `custom.ops.incident.state` (Selection firing/acknowledged/resolved, indexed) — lifecycle.
- `custom.ops.incident.raw_payload` (Text, truncated to 10000 chars) — forensic preservation.
- `custom.ops.incident.name` (Char, computed `[<tenant.slug|global>] <alert_name>`) — display.

## Public Methods
- `custom.ops.tenant.health._cron_collect_snapshots()` (`@api.model`) — 60s cron entrypoint.
- `custom.ops.tenant.health._collect_metrics_bulk(client)` — runs the 11 PromQL queries (cpu_pct, memory_pct, etc.) and aggregates by `db` label.
- `custom.ops.tenant.health._classify_backup(last_backup_at)` (static) — ok/stale/failed bucketing.
- `custom.ops.capacity.forecast._cron_regenerate()` (`@api.model`) — hourly forecast refresh.
- `custom.ops.capacity.forecast._tenant_history(tenant, metric)` — pulls last 30d of snapshots as `[{ts, value}]`.
- `custom.ops.capacity.forecast._call_predictor(url, metric, history)` (static) — POSTs to predictor, returns dict or None on failure.
- `custom.ops.incident.ingest_alertmanager_payload(payload)` (`@api.model`) — upsert all alerts in payload.
- `custom.ops.incident._upsert_one_alert(alert)` — fingerprint-keyed upsert; auto-schedules ack activity on new firing.
- `custom.ops.incident.action_acknowledge()` / `action_resolve()` — manual lifecycle moves.
- `PrometheusClient.from_env(env)` (classmethod) — constructs from `custom_ops_monitor.prometheus_url` + `_timeout_s` config.
- `PrometheusClient.query(promql)` / `query_range(promql, start, end, step)` — instant + range queries.
- `PrometheusClient.values_by_label(result, label)` (static) — flattens to `{label_value: float}`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_super_admin` (for `tenant.registry`), `mail`, `web`.
- **Inherits from:** `mail.thread`, `mail.activity.mixin` (on `custom.ops.incident`).
- **Extended by:** `custom_hub_console` (reads `tenant_id.health_status` + last incident on the tenant dashboard), `custom_tenant_infra` (consumes `tenant.health` snapshots for canary healthcheck gating).
- **External calls:** GET to Prometheus `/api/v1/query` (default `http://prometheus:9090`), POST to predictor `/forecast` (default `http://predictor:8000/forecast`). Inbound: Alertmanager webhook to `/api/ops/alert`.
- **Cross-vertical:** generic ops infra; not vertical-locked.

## Gotchas
- **Snapshots accumulate unbounded** — there is no retention cron on `custom.ops.tenant.health`. At 60s × tenants × hours-per-day, this table grows fast. Plan a partition or purge externally.
- **PromQL queries hardcode `by (db)`** — your Prometheus exporters MUST emit a `db` label matching `tenant.registry.db_name`, or `values_by_label("db")` returns empty and snapshots are all zeros.
- **`health_score` formula has hardcoded thresholds and weights** in `_compute_health` — not configurable.
- **`_classify_backup` uses `datetime.now()` (naive)** — comparing to a stored UTC datetime may drift by the TZ offset of the worker process.
- **Predictor failure is silent** — `_call_predictor` returns `None` on `URLError`/`ValueError`, the cron skips that tenant/metric and moves on. No telemetry of predictor outages.
- **`_schedule_ack_activity` picks the first user** in `group_ops_engineer.users` — no round-robin, no escalation. If the group is empty, no activity is scheduled (silently).
- **Resolved alerts for unknown fingerprints are dropped** by `_upsert_one_alert` — if Alertmanager restarts and re-sends only resolution events for previously fired alerts, those resolutions are lost.
- **`fingerprint_uniq` SQL constraint** means a second alert with no fingerprint (`fp == ""`) collides with the first — Alertmanager normally always sends fingerprints, but malformed payloads could break upsert.
- **PrometheusClient uses `urllib.request` (stdlib)** to avoid adding `requests`. Read timeout default 5s — slow Prometheus instances will fail queries.
- **The Alertmanager webhook controller has a fallback `secure_endpoint` no-op** when `custom_core` import fails — leaves the endpoint unauthenticated on broken installs.

## Out of Scope
- **Direct paging / on-call routing** — webhook drops a `mail.activity`; PagerDuty/Opsgenie integration is not here.
- **Trace / log ingestion** — metrics + alerts only.
- **SLO computation** — `health_score` is a heuristic, not an SLO/SLA tracker.
- **Per-tenant alert routing rules** — all alerts hit the same Alertmanager scope.
- **Retention / archival** — see gotcha above.
