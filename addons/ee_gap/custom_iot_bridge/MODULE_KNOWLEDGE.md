---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_iot_bridge
manifest_version: 19.0.0.1.0
---

# custom_iot_bridge

## Purpose
A lightweight device-data gateway for Odoo. Represents physical devices (`iot.device`), ingests timestamped sensor readings (`iot.reading`) via a tokenised public webhook (`POST /iot/ingest`), and evaluates user-defined comparator thresholds (`iot.threshold`) that auto-raise `alert_active` and post chatter notifications on the device when breached.

This is the **canonical IoT capability module** for the platform. BRD analyzers should map any requirement about "sensor ingestion / device gateway / threshold alert / telemetry" to this module — vertical modules (cold-chain, manufacturing, fleet, smart-building) should depend on it and add domain models that reference `iot.device` / `iot.reading`, not re-implement the ingest endpoint.

## Business Flow
- Operator registers a device on `iot.device` (kind=sensor/gateway/plc/camera/other). `create()` auto-mints an `api_token` via `secrets.token_urlsafe(32)`; status starts `offline`.
- Optional: configure one or more `iot.threshold` rules on the device — pick a `metric` (free-form key, e.g. `temperature_c`), a `condition` (`>` `<` `>=` `<=` `==`), `threshold_value`, `severity`, and `notify_user_ids`.
- Device firmware POSTs each reading to `/iot/ingest` with header `X-Device-Token: <api_token>` and JSON body `{metric, value, unit?, recorded_at?, extra?}`. The controller `IotIngestController.ingest()` validates the token, creates an `iot.reading` (using context flag `iot_internal_write=True` to bypass the immutable guard), bumps `device.last_seen_at` + `device.status=online`, then calls `iot.threshold.evaluate(reading)`.
- `iot.threshold.evaluate(reading)` searches all active thresholds on `(device_id, metric)`, applies the comparator, and: (a) if breached and not already alerting → set `alert_active=True`, `alert_since=now`, post chatter on the device, audit `iot_threshold_trip`; (b) if cleared and was alerting → clear `alert_active`, post "back within range", audit `iot_threshold_clear`; (c) otherwise just stamp `last_evaluated_at`.
- `action_rotate_token()` on the device regenerates `api_token` (breaks existing firmware until reconfigured).
- Readings and threshold-evaluation history are surfaced as list views; the device form shows `reading_count` + `alert_count` computed counters.

## Key Models
- `iot.device` — Physical device registration; carries the secret API token and online/offline status.
- `iot.reading` — Immutable timestamped (metric, value, unit) row; arbitrary JSON `extra` for raw payload. Write/unlink blocked unless `iot_internal_write` context flag is set.
- `iot.threshold` — Per-device, per-metric comparator rule with hysteresis-free alert flip-flop (`alert_active`).

## Important Fields
- `iot.device.code` (Char, unique, indexed) — stable external identifier.
- `iot.device.kind` (Selection sensor/gateway/plc/camera/other).
- `iot.device.api_token` (Char, readonly, indexed) — only authentication for the webhook; auto-generated, rotatable.
- `iot.device.status` (Selection online/offline/decommissioned, tracked) — bumped to online on every successful ingest.
- `iot.device.last_seen_at` (Datetime, readonly) — touched on each ingest.
- `iot.reading.metric` (Char, indexed) — free-form key (no enum); the threshold join key.
- `iot.reading.value` (Float, required).
- `iot.reading.recorded_at` (Datetime, indexed) — defaults to ingest time; the device may override via ISO-8601 payload.
- `iot.reading.extra` (Json) — raw device payload.
- `iot.threshold.condition` (Selection `>`/`<`/`>=`/`<=`/`==`) + `threshold_value` (Float) — the comparator.
- `iot.threshold.alert_active` (Boolean, readonly) + `alert_since` (Datetime, readonly) — current trip state.
- `iot.threshold.severity` (Selection info/warn/critical) — informational only; no routing logic in this module.
- `iot.threshold.notify_user_ids` (M2m res.users) — declared, but actual notification beyond the device chatter post is left to downstream modules.

## Public Methods
- `iot.device.action_rotate_token()` — regenerate `api_token`, post chatter note.
- `iot.threshold.evaluate(reading)` (`@api.model`) — applies all thresholds to a reading; central alerting entry point.
- Controller: `POST /iot/ingest` (`type="jsonrpc"`, `auth="public"`, csrf disabled) — the device webhook. Returns `{"ok": True, "reading_id": ...}` or `{"error": "<reason>"}`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `mail`.
- **Inherits from:** `mail.thread` on `iot.device`; `pdp.audited.mixin` on `iot.threshold`.
- **Extended by:** any vertical that needs domain-specific IoT — e.g. cold-chain (link reading to shipment), manufacturing (link to work order), property (link to building/room). Pattern: add `iot.device.x_<domain>_ref_id` Many2one and a `iot.reading` post-hook or override `iot.threshold.evaluate` to also fan out to your domain alert table.
- **External calls:** **none from Odoo outbound.** All traffic is inbound webhook POST from devices.
- **Cross-vertical:** Single shared device registry. Vertical-specific dashboards/alerts should subscribe to the data here rather than fork.

## Gotchas
- **`/iot/ingest` is `auth="public"`** — the `X-Device-Token` header is the **only** authentication. Token theft = full data injection for that device. Rotate aggressively.
- **CSRF is disabled** (`csrf=False`) for the webhook — required for non-browser clients, but means any path that reaches the route is accepted; defence is the per-device token only.
- **Readings are immutable via `UserError`** at the model level (write/unlink raise unless `iot_internal_write=True` context is present). A `sudo()` write **from another module that does not set the flag will throw** — wrap external writes with `with_context(iot_internal_write=True)`.
- **`metric` is free-form Char** — no enum, no per-device whitelist. Typos silently create disjoint metric streams.
- **No batching:** the webhook accepts one reading per call. High-frequency devices must self-throttle.
- **`recorded_at` from the device replaces server time** if supplied; trusts the device clock. Out-of-order or back-dated readings are accepted as-is.
- **Threshold alert state is per-rule, not per-reading window** — a single anomalous reading flips `alert_active=True` and a single in-range reading flips it back; there is no hysteresis, debounce, or rolling-window.
- **`notify_user_ids` is declared but unused beyond the chatter `message_post` on the device** — no email / activity / dedicated mail.message to the listed users is generated by this module. Downstream wiring required.
- **No company_id / multi-company scoping on `iot.device`** — the module is tenant-isolated only via DB-per-tenant.

## Out of Scope
- **MQTT / CoAP / OPC-UA brokers** — only HTTP JSON POST is implemented. Devices behind those protocols need a separate adapter that translates to the webhook.
- **Time-series retention / down-sampling / TSDB integration** — readings are stored as plain rows; PostgreSQL is the only store.
- **Push notifications, email alerts, SMS** — only chatter `message_post` is emitted; no routing.
- **Anomaly detection (statistical or ML)** — only static comparator thresholds. AI-driven anomalies live in `custom_ai_features` (`ai.anomaly.finding`) and are independent.
- **Device provisioning / firmware OTA** — out of scope; only registration + token issuance.
- **Calibration / unit conversion** — `unit` is a free-text label, never used for math.
