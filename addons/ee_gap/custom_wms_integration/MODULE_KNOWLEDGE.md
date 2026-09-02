---
status: draft
generated_at: 2026-07-22T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_wms_integration
manifest_version: 19.0.0.1.0
---

# custom_wms_integration

## Purpose
Two-way integration between Odoo Inventory and an external WMS or host system — SAP (WM/EWM behind a PI/CPI or REST facade) and generic REST hosts. Inbound, the host pushes ASNs, delivery orders and acknowledgements into Odoo over an HMAC-signed REST API and reads on-hand stock. Outbound, every warehouse event Odoo produces (goods receipt, putaway, pick, pack, goods issue, cycle-count adjustment) is persisted in a durable outbox and drained through a `custom_adapter_framework` adapter by a cron. The module owns no warehouse logic of its own — it is a translation and delivery layer.

## Business Flow
- **Host → Odoo (ASN).** `POST /api/wms/asn` with `{external_ref, partner_ref, warehouse_code, expected_date, lines[]}`. `stock.picking._wms_upsert_from_host(payload, "incoming")` resolves the operation type from `warehouse_code` (`stock.warehouse.in_type_id`), resolves partner and SKUs through `wms.integration.mapping`, and creates a **draft** incoming picking stamped with `wms_external_ref`. A repeat POST with the same `external_ref` finds the existing picking, wipes its draft moves and rebuilds them — one picking, never two. Once the picking has left `draft`/`confirmed` the payload is ignored and reported back as a warning; the host cannot rewrite work in progress.
- **Host → Odoo (DO).** `POST /api/wms/do` — identical path, `outgoing`, `stock.warehouse.out_type_id`.
- **Host → Odoo (stock query).** `GET /api/wms/stock?sku=&location_code=&warehouse_code=&limit=&offset=` reads `stock.quant` restricted to internal locations, paginated (default 200, hard max 1000) and translated back to host codes.
- **Odoo → Host.** `stock.picking.button_validate()` runs `super()` first; for every picking that actually reached `done` it enqueues `wms.integration.event` rows: `goods_receipt` (incoming), `goods_issue` + `pick_confirmed` (outgoing), `putaway_done` (internal), plus one `pack_created` per result package. `custom.cycle.count.adjustment.action_post` — when that module happens to be installed — enqueues `stock_adjustment`.
- **Drain.** The cron `wms.integration.event._cron_drain_outbox()` (every 5 minutes) walks pending rows oldest-first, resolves the adapter config, and calls `WmsHostAdapter.push_event()`. The framework does the retry/backoff, the circuit breaker and the `custom.adapter.call.log` write. The drain stops early when the breaker is open.
- **Ack.** `POST /api/wms/ack` with `{external_ref | external_refs[], host_ref}` moves the outbox rows to `acked` and stamps `acked_at`. Unknown references are reported in `data.unknown` rather than erroring.

## Key Models
- `wms.integration.event` — the outbound outbox. Append-only-ish: payload and source reference freeze at creation, only delivery bookkeeping mutates. Carries the cron, the ack handler, and (via `_register_hook`) the cycle-count patch.
- `wms.integration.mapping` — host-code ↔ Odoo-record translation for `product.product`, `stock.location`, `res.partner`, with a natural-key fallback so most tenants need very few rows.
- `stock.picking` (extension) — `wms_external_ref` idempotency key, the `button_validate` hook, the ASN/DO upsert, and the payload builders.
- `WmsHostAdapter` / `WmsSapHostAdapter` — plain Python `BaseAdapter` subclasses (not Odoo models), registered as `wms_host` and `wms_sap_host`.

## Important Fields
- `wms.integration.event.name` — `WMSEVT/<year>/######` from `ir.sequence` code `wms.integration.event`; doubles as the default `external_ref`.
- `wms.integration.event.event_type` — `goods_receipt` / `putaway_done` / `pick_confirmed` / `pack_created` / `goods_issue` / `stock_adjustment`. Kept in one place (`EVENT_TYPES` in `models/wms_host_adapter.py`) so the Selection and the adapter endpoint map cannot drift apart.
- `wms.integration.event.state` — `pending` → `sending` → `sent` → `acked`, or `failed` after `MAX_ATTEMPTS` (8). Indexed; the cron's only selector.
- `wms.integration.event.payload` — `fields.Json`. The `data` sub-object of the wire envelope, not the whole envelope (see `_envelope()`).
- `wms.integration.event.external_ref` — correlation key the host echoes on `/api/wms/ack`. Defaults to `name`; `pack_created` events use `<picking>/<package>`.
- `wms.integration.mapping.direction` — `inbound` / `outbound` / `both`. A row only applies to lookups in its own direction, which lets a tenant translate asymmetrically.
- `wms.integration.mapping.company_id` — empty means "all companies"; a company-specific row wins over a global one (`order="company_id desc"`).
- `stock.picking.wms_external_ref` — the ASN/DO idempotency key, indexed, `copy=False`.

## Public Methods
- `wms.integration.event.enqueue(event_type, record=None, payload=None, external_ref=None, company=None)` — creates one row; raises `UserError` on an unknown `event_type`.
- `wms.integration.event._safe_enqueue(...)` — same, inside a savepoint, swallowing everything. **The only form a business hook may call.**
- `wms.integration.event._cron_drain_outbox(limit=200)` — cron entry point; returns the number delivered.
- `wms.integration.event._ack(external_ref, host_ref=None)` — returns the acknowledged recordset, empty when unknown.
- `wms.integration.event._adapter_config(company=None)` — resolves the `custom.adapter.config` to push through.
- `wms.integration.mapping._resolve(external_code, model, company=None, direction="inbound")` — host code → recordset; explicit row, then natural key, then empty.
- `wms.integration.mapping._external_code_for(record, direction="outbound")` — the reverse.
- `stock.picking._wms_upsert_from_host(payload, direction)` → `(picking, created, warnings)`.
- `stock.picking._wms_picking_payload(event_type)` / `_wms_enqueue_validation_events()` / `_wms_enqueue_package_events()`.
- `WmsHostAdapter.push_event(event_type, payload)` plus `push_goods_receipt` / `push_putaway_done` / `push_pick_confirmed` / `push_pack_created` / `push_goods_issue` / `push_stock_adjustment`.

## Integration Points
- **Inbound auth** — every route is wrapped in `@secure_endpoint("wms")` from `custom_core.controllers.secure_endpoint`. Scope configuration lives in `ir.config_parameter`:
  - `custom_core.secure_endpoint.wms.secret` — **required**; without it every call is rejected with `NO_SECRET_CONFIGURED` (fail-closed, and that is the shipped default).
  - `custom_core.secure_endpoint.wms.allowed_cidrs` — optional comma-separated CIDR/IP allowlist; empty means "any source IP".
  Callers send `X-Timestamp` (unix seconds, ±300s drift) and `X-Signature` = `hex(HMAC_SHA256(secret, ascii(timestamp) + raw_body))`. Nonces are replay-protected for 600s (process memory, Redis-backed when `redis_url` is configured). Accepted and rejected calls both land in `custom.adapter.call.log` when a `custom.adapter.config` named `secure_endpoint:wms` exists.
- **Outbound** — `custom_adapter_framework`: `@register_adapter("wms_host")` and `@register_adapter("wms_sap_host")`, driven by `custom.adapter.config` rows of the same names (seeded **disabled** in `data/adapter_config_data.xml`). Pin the one to use with `ir.config_parameter` `wms_integration.adapter_config`; leave it empty to take the first active WMS-typed config. Secrets live under the config's `credential_ref` key (`wms_integration.host_secret` / `wms_integration.sap_secret`).
- **`custom_wms_cycle_count`** — an *optional* peer, deliberately **not** in `depends`. The `stock_adjustment` hook is installed at registry-ready time by `wms.integration.event._register_hook()`, which patches `custom.cycle.count.adjustment.action_post` only if that model is in the registry.
- **`custom_pdp_audit`** — both models inherit `pdp.audited.mixin`.
- **`purchase` / `sale_management`** — declared dependencies so `origin` / partner references line up with PO and SO documents; no field extensions on either yet.

## Gotchas
- **Routing type is `json2`, not `json`.** In Odoo 19 `type="json"` is a deprecated alias for `type="jsonrpc"`, which forces POST (so `GET /api/wms/stock` would be impossible), re-wraps the return value in a `{"jsonrpc", "result"}` envelope, and json-dumps the `Response` object `@secure_endpoint` returns on rejection. `json2` returns dicts verbatim and passes `Response` objects through. This is a deliberate deviation from the original spec.
- **`json2` does not populate `request.params` from the query string** — only from the JSON body and path args. `GET /api/wms/stock` reads `request.httprequest.args` directly.
- **The two error envelopes differ.** Our handlers answer `{"status","data","error":{"code","message"}}`. A rejection by `@secure_endpoint` happens *before* our code runs and answers the platform's `{"ok": false, "error_code": ...}` with HTTP 401/403. Host clients must handle both.
- **`_sql_constraints` is silently ignored in Odoo 19** — it produces a warning and no constraint. `wms.integration.mapping` uses `models.Constraint` (`_external_code_uniq`) instead. `stock.picking.wms_external_ref` deliberately has **no** DB uniqueness constraint (it would collide with `copy=False` duplicates across companies); idempotency is enforced by search inside `_wms_upsert_from_host`, which means two *simultaneous* ASN POSTs with the same `external_ref` could still race. Serialise the host, or add a `models.Constraint` on `(company_id, wms_external_ref)` if the host cannot.
- **Odoo 19 renamed `stock.quant.package` → `stock.package`** (`stock.package.type` for types). `_wms_enqueue_package_events` reaches packages via `move_line_ids.result_package_id` and never names the model, so it is rename-proof.
- **`stock.move` has no `name` field in Odoo 19** — `_wms_sync_lines` sets `reference` and `description_picking`. Passing `name` raises `ValueError: Invalid field 'name' in 'stock.move'`.
- **The hooks are double-guarded on purpose.** `button_validate` wraps the whole enqueue block in `cr.savepoint()` + bare `except`, and `_safe_enqueue` wraps the INSERT again. The savepoint is not decoration: a failed INSERT leaves PostgreSQL in `InFailedSqlTransaction` and every later statement in the business transaction dies with it (the same failure mode as the `pdp.audit_log` incident).
- **`button_validate` may return a wizard action** (backorder / immediate-transfer). Only pickings whose `state` is actually `done` after `super()` are enqueued, so a picking that raises a backorder wizard emits its events on the *second* call.
- **Auth is `none`, so `request.env.uid` is `None`.** Every handler goes through `sudo()` or `request.env(su=True)` before touching `env.company`; reading `env.company` on a `uid=None` environment raises.
- **The outbox never gives up quietly.** With no active adapter config, rows stay `pending` with `last_error = "No active WMS adapter config"` and `attempts` untouched — they accumulate rather than fail. Watch the "Pending" filter, which is the default on the menu action.
- **`_register_hook` patching is class-level.** If a future module also patches `custom.cycle.count.adjustment.action_post`, ordering is registry-load order; the `_wms_integration_patched` flag prevents double-wrapping within one load only.
- **`expiry` on ASN lines is dropped unless `product_expiry` is installed** (that module owns `stock.move.line.expiration_date`); the API returns it as a warning rather than failing the ASN.

## Out of Scope
- No pull/polling from the host — inbound is push-only (the host calls us).
- No master-data synchronisation (products, partners, locations, UoMs are never created from a host payload; an unresolvable code becomes a warning, not a new record).
- No automatic confirmation or reservation of ASN/DO pickings; they land in `draft` for a warehouse user to process.
- No SAP IDoc/RFC/BAPI wire formats — the SAP adapter speaks JSON over HTTP to a facade. Producing real IDoc XML is a facade concern.
- No queue_job integration; the drain is a plain `ir.cron`, deliberately, so the module installs on tenants without OCA `queue_job`.
- No lot/serial reconciliation beyond a `lot_name` hint on ASN move lines; the lot hint is best-effort and is dropped (with a warning) if the operation type forbids it.
- No outbound stock-level snapshot; the host reads on-hand via `GET /api/wms/stock`.
- Not verified against a live host or a live database — this module has never been installed or upgraded on any DB.
