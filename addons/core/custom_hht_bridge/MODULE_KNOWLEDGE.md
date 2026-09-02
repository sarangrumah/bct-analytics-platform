---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_hht_bridge
manifest_version: 19.0.0.1.0
---

# custom_hht_bridge

## Purpose
Brings physical handheld terminals (Zebra TC21/TC52/TC72, Honeywell CT40, generic Android with DataWedge, plus generic browser PWA) into the Odoo platform as first-class scan-aware clients. Provides a device enrollment registry with HMAC `api_key`/`api_secret` and CIDR allow-listing, an append-only scan audit log with GPS + payload, an offline sync queue with idempotent `client_id` de-duplication, an OWL PWA shell served at `/hht/`, a `@secure_endpoint('hht')`-protected REST API at `/api/hht/*`, and a DataWedge ingest endpoint for thin keyboard-wedge scanners.

## Business Flow
- Admin enrolls a device: creates `hht.device` (name, `device_id` serial, model, `tenant_id`, `user_id` default operator, optional `allowed_cidrs`). `create()` auto-generates `api_key = secrets.token_hex(16)` and `api_secret = secrets.token_hex(32)`. `api_secret` is write-protected — `write()` raises `UserError` unless context `hht_allow_secret_write=True`; rotation goes through `action_regenerate_secret()` (gated on group `custom_hht_bridge.group_hht_admin`).
- Operator opens the PWA at `/hht/` on the device. PWA reads `api_key`/`api_secret` from local storage, signs every request to `/api/hht/*` with HMAC-SHA256 over `<timestamp>.<body_bytes>`, adds headers `X-Device-Key: <api_key>` + `X-Signature` + `X-Timestamp`.
- `@secure_endpoint('hht')` (from `custom_core`) validates timestamp drift (±300s), HMAC against the scope secret, nonce replay (Redis-backed when configured), and CIDR allowlist before dispatching.
- Each scan POSTs to `/api/hht/scan` (or DataWedge endpoint) with `barcode`, `action` (receipt/issue/transfer/count/handover/lookup), optional `location_id`/`qty`/`lot_id`/`picking_id`. Server writes `hht.scan.log` row (sha256-indexed device+time index `hht_scan_log_device_time_idx`), updates `hht.device.last_seen_at`/`last_action_at` via `_touch_seen()`.
- Offline scans accumulate in IndexedDB on the device; when connectivity returns, the PWA flushes them to `/api/hht/sync` as `hht.sync.queue` rows. The unique constraint `(device_id, client_id)` enforces idempotent dedup; the apply cron processes queued items into business records (transfers, counts, BAST documents).
- `hht.device.action_view_scan_logs()` / `action_view_sync_queue()` open per-device drilldowns. `_compute_scan_count_today` shows daily volume; computed `status` becomes `quarantined` when `scan_count_today > 10000` (heuristic anomaly).
- Daily cron `_cron_purge_old_queue(days=30)` deletes applied/deduped queue rows older than 30 days.

## Key Models
- `hht.device` — Enrolled physical/browser device. Inherits `mail.thread`, `mail.activity.mixin`. Holds HMAC credentials, tenant link, optional CIDR allowlist, telemetry.
- `hht.scan.log` — Append-only audit log; one row per scan/lookup. Indexed by `(device_id, scanned_at DESC)` via `init()` raw SQL.
- `hht.sync.queue` — FIFO journal of operations queued offline. `(device_id, client_id)` unique → idempotent. Lifecycle: queued → processing → applied/failed/deduped.

## Important Fields
- `hht.device.device_id` (Char, indexed, unique per tenant) — physical serial (e.g. `TC52-SN12345`) or browser fingerprint.
- `hht.device.model` (Selection zebra_tc21/tc52/tc72/honeywell_ct40/generic_browser/other) — hardware class.
- `hht.device.tenant_id` (M2o tenant.registry, set_null) — tenant ownership.
- `hht.device.api_key` (Char, readonly, copy=False, indexed) — `secrets.token_hex(16)`, looked up by `_find_by_api_key`.
- `hht.device.api_secret` (Char, readonly, copy=False) — `secrets.token_hex(32)`; HMAC shared secret. WRITE-PROTECTED.
- `hht.device.allowed_cidrs` (Char) — CSV of CIDRs/IPs; validated by `_check_allowed_cidrs` via `ipaddress`.
- `hht.device.enabled` (Boolean, tracking) — kill switch.
- `hht.device.last_seen_at` / `last_action_at` / `last_action_summary` (readonly) — telemetry.
- `hht.device.scan_count_today` (Integer, computed) — drives the `quarantined` heuristic.
- `hht.device.status` (Selection active/disabled/quarantined, computed) — derived from `enabled` and `scan_count_today > 10000`.
- `hht.scan.log.barcode` / `action` / `location_id` / `qty` / `lot_id` / `picking_id` — scan facts.
- `hht.scan.log.result` (Selection ok/error/pending_sync, indexed) — outcome.
- `hht.scan.log.payload` (Json) — raw request payload for forensics.
- `hht.scan.log.client_ip` (Char) — extracted from `X-Forwarded-For` / `remote_addr`.
- `hht.sync.queue.client_id` (Char, indexed) — client-generated stable id; uniqueness `(device_id, client_id)` enforces idempotency.
- `hht.sync.queue.state` (Selection queued/processing/applied/failed/deduped, indexed) — processing lifecycle.
- `hht.sync.queue.batch_id` (Char, indexed) — groups related items.

## Public Methods
- `hht.device.action_regenerate_secret()` — group-gated; writes fresh `api_secret` via `hht_allow_secret_write` context; posts chatter note.
- `hht.device.action_view_scan_logs()` / `action_view_sync_queue()` — drill-downs.
- `hht.device._touch_seen(summary=None)` — controller helper; bumps `last_seen_at` and optionally `last_action_*`.
- `hht.device._find_by_api_key(api_key)` (`@api.model`) — resolves a device from header `X-Device-Key`, enabled only.
- `hht.device._find_by_serial(serial)` (`@api.model`) — alternate lookup.
- `hht.device._cron_reset_scan_count_today()` (`@api.model`) — hourly cron hook (invalidates compute cache).
- `hht.sync.queue.action_retry_failed()` — re-queues failed items.
- `hht.sync.queue._cron_purge_old_queue(days=30)` (`@api.model`) — daily purge of applied/deduped > 30d.
- Controllers (in `controllers/api.py`, `datawedge.py`, `pwa_shell.py`): `/api/hht/scan`, `/api/hht/sync`, `/api/hht/datawedge`, plus `/hht/` PWA assets, `/manifest.webmanifest`, service worker.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_bast`, `custom_barcode`, `stock`, `mail`, `web`.
- **Inherits from:** `mail.thread`, `mail.activity.mixin` (on `hht.device`).
- **Extended by:** vertical modules that bind scan actions to their own business records (e.g. WMS scan-to-pick).
- **External calls:** none outbound. Inbound: PWA + DataWedge POSTs.
- **Cross-vertical:** generic — anywhere physical scanners need to feed Odoo. Common scan targets: `stock.picking`, `stock.lot`, `custom.bast.document`.

## Gotchas
- **`api_secret` is write-protected by default**; an admin import or manual edit via the UI will raise `UserError`. Only `action_regenerate_secret` (with `hht_allow_secret_write=True` context) can change it. Initial `create()` is allowed because the values come from `secrets.token_hex`.
- **`status` is `store=False`** — you cannot filter searches on `status="quarantined"`; rules must filter on `enabled` + `scan_count_today` ranges instead.
- **`_compute_scan_count_today` is a search-count per record on demand** — opening a list view with N devices triggers N queries. Compute is `store=False`.
- **`hht_scan_log_device_time_idx` is created via raw SQL in `init()`** — `CREATE INDEX IF NOT EXISTS` is idempotent but bypasses Odoo's index management.
- **`hht.scan.log.payload` is `fields.Json`** — raw request body verbatim; may contain PDP-sensitive fields. No automatic masking.
- **Quarantine heuristic is hardcoded** at >10000 scans/day; not configurable.
- **`_find_by_api_key` uses `sudo()`** — bypasses record rules. Auth lives entirely in `@secure_endpoint`.
- **`device_id_tenant_uniq` constraint** allows the same `device_id` to be re-used across tenants — be careful when re-assigning hardware between tenants.
- **Sync queue dedup** is per-`(device_id, client_id)` — a poorly written PWA that reuses `client_id` across actions will see writes silently dropped via the unique constraint.

## Out of Scope
- **The PWA JavaScript app itself** — this module ships the OWL/JS assets and serves them, but the documentation here covers only the server-side Python.
- **Per-scan business processing** — the bridge writes logs and queue rows; turning them into `stock.move`/`stock.picking` updates is left to processor code (in `controllers/api.py._handle_scan` dispatcher and downstream modules).
- **Device provisioning / enrollment via QR** — manual `hht.device.create` only in the current build.
- **Multi-tenant key isolation** — the `@secure_endpoint('hht')` scope secret is platform-wide; per-device HMAC is enforced by `api_secret` but the wrapping `secure_endpoint` decorator uses a single shared scope secret.
