---
status: active
generated_at: 2026-07-21
generator: manual
module: custom_esb_connector
manifest_version: 19.0.0.1.0
---

# custom_esb_connector

## Purpose

The Odoo side of the ESB edge. **ESB Core is the source of truth for stock** in the EFN
(the group F&B) vertical; this module mirrors ESB master data and balances into Odoo, and pushes
documents back as native ESB records. It deliberately contains **no business logic** —
counting, forecasting and replenishment live in the consuming vertical module
(`custom_fnb_stock_ops`).

API reference: [`docs/integrations/esb-core-api.md`](../../../docs/integrations/esb-core-api.md).
Raw captured spec: `docs/integrations/esb/esb-core.apidoc.json`.

## Business flow

```
ESB Core ──pull──▶ custom.esb.branch / .location / .purpose / .document.template
                   product.product (x_esb_*) + custom.esb.product.detail
ESB Core ──pull──▶ custom.esb.stock.snapshot        (derived from the movement report)
ESB Core ◀──push── custom.esb.outbox                (item journal / PR / GTR / PO)
```

Everything degrades gracefully. With no active `custom.adapter.config` the crons log `skipped`
and return; with `esb.push_enabled` off no outbound write leaves Odoo at all. The module is
installable and harmless long before ESB credentials exist.

## Key models

| Model | Role |
|---|---|
| `custom.esb.session` | Access/refresh token lifecycle behind a row lock. Static API key mode supported. |
| `custom.esb.branch` / `.location` | Mirrors of ESB outlets and their stock locations, with an *optional* link to `stock.warehouse` / `stock.location`. |
| `custom.esb.purpose` | ESB adjustment reasons; each carries a COA, so the purpose picked on an item journal routes the variance in ESB's GL. Flag one as default gain and one as default loss. |
| `custom.esb.document.template` | ESB request templates (template-mode item journals and purchase requests). |
| `custom.esb.product.detail` | One row per ESB `productDetailID` — a product in a specific unit. |
| `custom.esb.stock.snapshot` | Read-only on-hand mirror per (branch, location, product). |
| `custom.esb.outbox` | The single outbound path, with the idempotency guard. |
| `custom.esb.sync.log` | Per-feed outcome log (coarser than `custom.adapter.call.log`). |
| `custom.esb.master.sync` | AbstractModel holding the feed runners and cron entry points. |

Adapters: `esb_core`, `esb_corev1`, `esb_oms` — all `EsbCoreAdapter` subclasses registered on
`custom_adapter_framework`.

## Important fields

- `product.product.x_esb_product_detail_id` — the **stock-unit** `productDetailID`. This, not
  `productID`, is what every ESB transactional endpoint wants. Use `product._esb_detail_id(kind)`
  to resolve the purchase/transfer/base unit instead.
- `custom.esb.outbox.idempotency_key` — generated `ODOO-<hex>`, stamped into the document's
  `additionalInfo` and used to detect an already-created document.
- `custom.esb.outbox.adopted` — true when the guard found the document already existed and
  adopted it rather than creating a duplicate.
- `custom.esb.session.credential_ref` — an `ir.config_parameter` **key**. The secret is never
  stored on the record.

## Public methods

- `custom.esb.master.sync.action_sync_now()` / `_cron_sync_masters()`
- `custom.esb.stock.snapshot.refresh_branch(branch)` — rebuild from the movement report
- `custom.esb.stock.snapshot.refresh_one(location, product)` — authoritative single-SKU read
- `custom.esb.stock.snapshot.qty_for(location, product)` → float **or `None`**
- `custom.esb.outbox.enqueue(doc_type, payload, res_model=, res_id=)`
- `custom.esb.session.action_test_connection()`

## Gotchas

1. **`HTTP 200` with `status: "fail"` is a failure.** `_handle_response` rewrites envelope
   failures to 401/422 so `BaseAdapter` treats them as permanent — retrying a validation error
   would only trip the circuit breaker.
2. **ESB evicts sessions.** *"A successful API login will log you out of any existing ESB Core
   session using the same credentials."* Use one dedicated ESB user for Odoo, never a human's
   account, or a human logging into the ESB web UI will break the integration. Token rotation is
   serialised on a `FOR UPDATE` row lock; an eviction clears **both** tokens and re-logins.
3. **No bulk stock-on-hand endpoint exists.** Balances are the last `qtyBalance` per
   (branch, location, productDetailID) in a movement-report window. A product that did not move
   in the window has **no snapshot row**, and `qty_for()` returns `None`. Callers must treat that
   as *unknown* — booking an adjustment against an assumed zero writes a fabricated entry into
   ESB's general ledger.
4. **Item journal `qty` is the signed delta**, not the counted quantity.
5. **No idempotency header on any ESB POST.** Hence the `additionalInfo` guard. If the lookup
   itself fails, the push **aborts** rather than posting blind.
6. **Three hosts, three adapter configs.** `/corev1/...` paths are documented inside the ESB Core
   docs but resolve against a different host.
7. `custom.esb.location.countable` — ESB item journals only accept warehouse- and kitchen-type
   locations; other locations will be rejected as an opname target.

## Configuration

`ir.config_parameter`, all off by default:

| Key | Effect |
|---|---|
| `esb.master_sync_enabled` | Master-data cron |
| `esb.snapshot_enabled` | Stock snapshot cron |
| `esb.snapshot_lookback_days` | Movement window (default 90) |
| `esb.push_enabled` | **Hard kill-switch for every outbound write** |
| `esb.auto_authorize_item_journal` | PATCH `/authorize` after creating an item journal |
| `esb.branch_whitelist` | Comma-separated branch codes to narrow the snapshot cron |
| `custom_esb_connector.esb_password` | The ESB password / static API key |

The three `ir.cron` records are all seeded `active=False`.

## Integration points

- **Consumes** `custom_adapter_framework` (`BaseAdapter`, `custom.adapter.config`,
  `custom.adapter.call.log`, circuit breaker) and `queue_job` (channel `root.esb`).
- **Provides** to downstream verticals: `custom.esb.outbox.enqueue(...)`,
  `custom.esb.stock.snapshot.qty_for(...)`, `product._esb_detail_id(...)`.
- **PDP**: models inherit `pdp.audited.mixin`; stored access tokens are readable only by
  `group_esb_admin`.

## Tests

70 tests, all against `MockEsbTransport` with fixtures transcribed verbatim from the ESB
documentation's own `Success-Response` examples (`tests/fixtures/`). No credentials required.

```
odoo -d <db> -u custom_esb_connector --test-enable --test-tags /custom_esb_connector --stop-after-init
```
