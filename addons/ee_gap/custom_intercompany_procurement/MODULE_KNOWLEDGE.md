---
status: draft
generated_at: 2026-07-02T07:54:45Z
generator: bootstrap-v1
module: custom_intercompany_procurement
manifest_version: 19.0.0.1.0
---

# custom_intercompany_procurement

## Purpose
This module automates the mirroring of purchase orders and stock pickings between sister companies within the group group. When a purchase order is confirmed in one company, a corresponding draft sales order is auto-created in the receiving sister company; when an outgoing picking is validated, a matching incoming picking is created in the receiving company. It also spawns internal asset-loan rental orders for drone-style loan flows. The base `account.intercompany.rule` (from `custom_accounting_full`) previously mirrored only the GL invoice; this module adds the procurement/logistics side.

## Business Flow
1. **Purchase Order Confirmation:**
   - A purchase order (PO) is confirmed in the issuing company (`purchase.order.button_confirm`).
   - The module resolves the receiving company from the PO partner's `commercial_partner_id` and searches for an active intercompany rule with `mirror_purchase_order = True`.
   - If found, it creates a draft sales order (SO) in the receiving company (`with_company`, `sudo`).

2. **Stock Picking Validation:**
   - An outgoing stock picking is validated in the issuing company (hook on `stock.picking._action_done`).
   - Only outgoing pickings with a partner are considered; the receiving company is resolved from the partner, and an active rule with `mirror_picking = True` is searched.
   - If found, it creates a matching incoming picking in the receiving company.

3. **Asset Loan Integration:**
   - When the mirrored SO in the receiving company is confirmed and carries the loan service line, the module auto-creates a draft internal asset-loan rental order (`rental.order`).
   - The physical asset moves via an Internal->Internal loan transfer (it never leaves the selling company's location tree and posts no COGS/valuation journal); only the service line is invoiced.

## Key Models
- **account.intercompany.rule** (`_inherit`) — Extends the base rule from `custom_accounting_full` with procurement-side toggles and asset-loan spawn configuration.
- **purchase.order** (`_inherit = ["purchase.order", "pdp.audited.mixin"]`) — On `button_confirm`, runs `_custom_run_ic_po_mirror` → `_custom_create_ic_mirror_so` to spawn the mirror SO in the receiving company. Audit classification `"financial"`.
- **stock.picking** (`_inherit = ["stock.picking", "pdp.audited.mixin"]`) — On `_action_done`, runs `_custom_run_ic_picking_mirror` → `_custom_create_ic_mirror_picking` to spawn the incoming mirror picking. Audit classification `"internal"`.
- **sale.order** (`_inherit`) — Holds mirror back-references and asset-loan logic; on `action_confirm` spawns the event-cycle asset loan.
- **rental.order** (`_inherit`) — Links back to the source intercompany SO (`sale_order_id`) and tags the loan cycle (`loan_type`).

## Important Fields
### account.intercompany.rule
- **mirror_purchase_order** (Boolean, default `False`): enable PO → SO mirroring.
- **mirror_picking** (Boolean, default `False`): enable outgoing → incoming picking mirroring.
- **target_warehouse_id** (Many2one `stock.warehouse`): receiving warehouse for mirrored pickings/SO; if empty, the first warehouse of the receiving company is used.
- **target_sale_journal_id** (Many2one `account.journal`): **(Reserved) Future** — declared but never read by any code.
- **spawn_rental_loan** (Boolean, default `False`): when the mirrored SO is confirmed with the loan service line, auto-create an internal asset-loan.
- **loan_service_product_id** (Many2one `product.product`, service): its presence on the SO marks it as an asset loan; its qty becomes the primary loan qty.
- **loan_asset_product_id** (Many2one `product.product`): the physical asset moved on loan (e.g. the drone).
- **loan_on_loan_location_id** (Many2one `stock.location`, internal): the location the asset sits in while on loan.

### purchase.order
- **x_custom_ic_mirror_so_id** (Many2one `sale.order`): the SO auto-generated in the sister company.
- **x_custom_ic_source_so_id** (Many2one `sale.order`): set when this PO was itself created by a mirror flow (back-reference).
- **x_custom_ic_rule_id** (Many2one `account.intercompany.rule`).

### sale.order
- **x_custom_ic_source_po_id** (Many2one `purchase.order`): the source PO that mirrored into this SO.
- **x_custom_ic_rule_id** (Many2one `account.intercompany.rule`).
- **loan_order_ids** (One2many `rental.order`, inverse `sale_order_id`).
- **loan_order_count** (Integer, computed).
- **is_asset_loan** (Boolean, computed): True when the SO carries the rule's loan service product.

### stock.picking
- **x_custom_ic_mirror_picking_id** (Many2one `stock.picking`): the incoming picking auto-generated in the sister company (idempotency guard).
- **x_custom_ic_source_picking_id** (Many2one `stock.picking`): back-reference when this picking is itself a mirror.
- **x_custom_ic_rule_id** (Many2one `account.intercompany.rule`).

### rental.order
- **sale_order_id** (Many2one `sale.order`): the intercompany SO this loan was spawned from.
- **loan_type** (Selection `preflight`/`event`): pre-flight and event handovers share one SO but are separate physical pickup/return cycles.

## Public Methods
- **purchase.order.button_confirm()**: after super, triggers the PO → mirror-SO flow.
- **stock.picking._action_done()**: after super, triggers the outgoing → mirror-picking flow.
- **sale.order.action_confirm()**: after super, spawns the `event`-cycle asset loan when the rule has `spawn_rental_loan` and the SO `is_asset_loan` (idempotent — one event loan per SO).
- **sale.order.action_create_preflight_loan()**: on-demand creation of a `preflight`-cycle loan; raises if no asset-loan rule/asset product, or if a pre-flight loan already exists.
- **sale.order.action_view_loan_orders()**: window action listing the SO's spawned loans.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_accounting_full`, `custom_rental`, `purchase`, `sale_management`, `stock`
- **Inherits (mixins):** `pdp.audited.mixin` on `purchase.order` and `stock.picking` (audit-trail).
- **Audit calls:** `_pdp_audit_write("ic_po_mirror_created", ...)` and `_pdp_audit_write("ic_picking_mirror_created", ...)`.
- **Extended by:** None
- **External calls:** None

## Gotchas
- **target_sale_journal_id is reserved/unused** — it is declared as "(Reserved) Future" and never read by code; do not treat a receiving-side sale journal as required.
- A receiving warehouse is required for both flows: `target_warehouse_id` if set, otherwise the first warehouse of the receiving company; if none exists the mirror raises (caught and posted to chatter).
- The picking mirror only fires for **outgoing** pickings that have a partner; the PO mirror is skipped if the PO is already mirrored or is itself a mirror (idempotency via the `x_custom_ic_*` fields).
- Asset-loan spawning is guarded on `is_asset_loan` (the SO must actually carry the loan service line) before a rental order is created.
- For asset-loan PO mirrors, physical (non-service) lines are deliberately skipped so no COGS/asset derecognition posts; a `UserError`/`ValueError` is raised **only** if this leaves no service line to mirror. Non-asset-loan mirrors carry all lines.
- Mirror failures are caught, logged, and posted to chatter — they do not block the source PO/picking.

## Out of Scope
- Does not mirror other document types beyond the two flows above (base invoice/GL mirroring lives in `custom_accounting_full`).
- Does not deliver physical products through the mirrored SO for asset-loan rules; the asset moves as an internal loan transfer.
