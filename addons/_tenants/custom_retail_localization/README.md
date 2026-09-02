# the apparel brand Localization (`custom_retail_localization`)

Tenant-scoped module bundling four the apparel brand requirements. Install only on the
the apparel brand databases (`prd_retail`, `rnd_retail`, `demo_retail`).

## 1. HS Code on the product master
Depends on native `stock_delivery`, which adds `product.template.hs_code`.
`views/product_template_views.xml` additionally surfaces the field on the
General Information tab (after Category) so it is captured during master-data
entry.

## 2. Receipt qty ≤ demand qty
`models/stock_picking.py` overrides `button_validate`: on any **incoming**
transfer, if a line's done quantity exceeds its demand (ordered) quantity, a
`UserError` is raised listing the offending products. Partial receipts
(done < demand, → backorder) are unaffected.

## 3. Inventory journal at Goods Receipt & Vendor Return (opt-in switch)
`models/stock_move.py` books inventory GL directly through the product
category pair `property_stock_valuation_account_id` +
`account_stock_variation_id` (this build has no stock input/output interim
accounts, so core real-time valuation posts nothing on its own). Behaviour is
governed by the `ir.config_parameter`
`custom_retail_localization.suppress_gr_journal` (default **OFF**):

- **Goods receipt** (source = supplier): `Dr Stock Valuation /
  Cr Stock Variation` for `move.value` — ref `GR-VAL:<move id>`.
- **Vendor return / RTV** (destination = supplier): the exact reverse,
  `Dr Stock Variation / Cr Stock Valuation` — ref `GR-RET-VAL:<move id>`.

Both fire on `_action_done`, only for `real_time` categories, and are
idempotent by `ref`. The stock `stock.move.value` is produced either way, so
on-hand quantity and value stay correct.

> **Switch ON (periodic mode):** both the receipt and the return journals are
> suppressed and the GL is trued up later by the **Inventory Reconciliation**
> tool (section 5). Use this only if perpetual GL posting is not wanted.

## 4. Payment Voucher & Payment Receipt
`reports/` adds two branded PDF documents on `account.payment`, bound to its
Print menu:
- **Payment Voucher** — vendor / outbound payments.
- **Payment Receipt** — customer / inbound payments.

Both use `web.external_layout` (company letterhead), an amount-in-words line and
prepared/approved/received signature blocks.

## 5. Periodic Inventory Reconciliation
`retail.inventory.reconciliation` (Accounting > Accounting > Inventory
Reconciliation) compares actual on-hand stock value against the GL balance per
valuation account and produces a DRAFT adjustment journal, to compensate for the
suppressed receipt journals (requirement 3). An inactive monthly cron is shipped.

## 6. Receipt lines must come from the PO
`models/stock_move.py` adds an `@api.constrains` on `stock.move`. On a **PO-linked
incoming** transfer, a line is only accepted when it **originates from the
purchase order** (it carries `purchase_line_id`). Any manually added line is
rejected at save time with a `ValidationError`.

- **Strict:** even a manual line for a product that *is* already on the PO is
  rejected — the line must be the one generated from the PO, not a hand-added
  duplicate. This is stronger than a product-membership check.
- Whether a receipt is "PO-linked" is resolved via
  `stock.picking._retail_purchase_orders()` (union of `purchase_id` and each
  move's `purchase_line_id.order_id`, so receipts grouping several POs work).
- **Standalone / non-PO incoming transfers** (no linked purchase order) are
  **not** restricted — there is no PO to check against.
- Enforcement is at save time (not only at validation), matching "cannot be
  added". Requires `purchase_stock` for the `purchase_id` / `purchase_line_id`
  links.
