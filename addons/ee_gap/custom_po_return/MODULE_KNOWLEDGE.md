---
status: draft
generated_at: 2026-07-03T00:00:00Z
generator: claude-code
module: custom_po_return
manifest_version: 19.0.0.1.0
---

# custom_po_return

## Purpose
Quantity-driven vendor return (RTV): the user states a total qty per product
to return to a supplier; the system allocates it FIFO across previous
POs/GRs at original PO prices, creates return pickings and draft vendor
credit notes, and shows which GR and vendor bill back every slice.

## Models
- `custom.po.return` — return document (vendor, date, lines, state
  draft → allocated → done / cancel; sequence `RTV/%(year)s/#####`).
- `custom.po.return.line` — product + qty_to_return (product base UoM).
- `custom.po.return.allocation` — one FIFO slice: `purchase_line_id`,
  `move_id` (source receipt move), `picking_id` (GR), `source_bill_id`,
  qty/price/amount, `return_move_id`, `return_picking_id`, `credit_note_id`.
- `purchase.order` inherit: `x_custom_po_return_ids/_count` smart button;
  `purchase.order.line` inherit: `x_custom_returned_qty`,
  `x_custom_returnable_qty` (non-stored).

## Key mechanics (Odoo 19 specifics — verified against core source)
- **Allocation** (`_allocate_line`): candidate POLs = same vendor + product +
  company, order state purchase/done, `qty_received > 0`; sorted by
  `date_approve`/`date_order`, order id, line id. Per POL, done incoming
  non-return moves sorted by date. Returnable per move =
  `move.quantity − Σ returned_move_ids (state≠cancel) − Σ pending allocations
  on draft/allocated returns` (so native returns AND other pending PO Returns
  are respected). Raises UserError with per-PO breakdown when short.
- **Stock**: drives core `stock.return.picking` wizard per source GR;
  `_create_return()` copies moves so `purchase_line_id` survives (field has
  no `copy=False`), sets `origin_returned_move_id`, `to_refund=True`
  (stock_account default). The return picking is left in its natural
  **draft/ready** state (core already `action_confirm`+`action_assign`ed it) —
  `custom.po.return` only wires `allocation.return_move_id/return_picking_id`;
  it does **not** force done qty or validate. The warehouse validates each
  return picking manually, and only then does the inventory move post (stock
  out + valuation journal) and `qty_received` decrement in core
  (`purchase_stock/.../purchase_order_line.py::_prepare_qty_received`).
  Over-return protection still holds pre-validation because
  `_get_move_returnable_qty` counts confirmed (non-done) `returned_move_ids`
  by their demand qty.
- **Valuation**: Odoo 19 has NO `stock.valuation.layer`; `stock.move.value`
  resolves via posted bill of the POL, else PO price
  (`_get_value_from_quotation`). Return moves carry `purchase_line_id`, so
  the out-value follows bill/PO price. If bill price ≠ PO price, stock value
  follows the bill while the credit note follows PO price (documented gap;
  v2 could price CN from the bill line).
- **Credit notes** (`_create_credit_notes`): draft `in_refund`, grouped per
  earliest posted `in_invoice` of the POL (`reversed_entry_id` set); slices
  without a posted bill go into one standalone CN per return. Lines carry
  `purchase_line_id` (nets `qty_invoiced`, verified in
  `purchase/models/account_invoice.py`), `tax_ids` from POL, price = PO price
  converted to base UoM via `product_uom_id._compute_price`.
- Works when GR journals are suppressed (the apparel brand localization) because it only
  reads `purchase_line_id.invoice_lines`, never stock journal entries.

## Gotchas
- `stock.move` still uses `product_uom`; only POL renamed to
  `product_uom_id`, and POL taxes are `tax_ids` (not `taxes_id`).
- `uom.uom.compare/is_zero/round` helpers exist in v19 — used instead of
  `float_compare`.
- All quantities on return line/allocation are in the **product base UoM**;
  wizard line quantities converted to `wline.uom_id` on validate.
- Mixed-currency allocations raise at compute time (v1 limitation).
- Validated returns cannot be cancelled/deleted (`_unlink_except_done`);
  reset-to-draft unlinks allocations (frees pending qty for other returns).
- `action_compute_allocation` counts allocations of the *same* return created
  earlier in the run (state draft) — needed when two lines share a product.
  `action_validate` re-verifies availability with `ignore_return=self`.

## Tests
`tests/test_po_return.py` (`post_install`), base class
`odoo.addons.purchase_stock.tests.common.PurchaseTestCommon` (loads
generic_coa, gives `_create_purchase/_receive/_create_bill` helpers).
UserError-raising compute calls are wrapped in `self.env.cr.savepoint()`
because partial allocations would otherwise leak into the test transaction.
