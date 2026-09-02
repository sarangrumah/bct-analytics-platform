---
status: draft
generated_at: 2026-07-22T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_wms_docs
manifest_version: 19.0.0.1.0
---

# custom_wms_docs

## Purpose
Warehouse **documents & labels**: the paper (and sticker) layer of the WMS stack. Ships four QWeb-PDF reports — Picking List, Packing List, Barcode List and Price Tag / Product Label — plus the Python helpers that compute their content, so the templates stay thin and the logic stays testable. All data shaping (walk-path ordering, package grouping, gross-weight arithmetic, label expansion) lives in Python; QWeb only iterates.

## Business Flow
- **Picking List** — printed from a transfer (`stock.picking`, bound to outgoing/internal via the report's `domain`). `_wms_pick_rows()` calls `_wms_pick_lines()` which sorts `move_line_ids` along an optimised walk path (source `location_id.complete_name`, then product `default_code`). Each row carries a walk sequence number, the source location plus its QR image, product code/name, lot/serial, expiry (when `product_expiry` is installed), demanded qty + UoM and an empty tick box. Footer prints line count, total qty and a picker signature line.
- **Packing List** — `_wms_packing_blocks()` groups `move_line_ids` by `result_package_id` (Odoo 19 model `stock.package`) and appends a final "Loose / unpacked" block for lines with no destination package. Every block prints the package name as **both** Code128 and QR, the `stock.package.type` name, its PxLxT dimensions and the computed gross weight (Σ product weight × qty + `package_type.base_weight`), then the contents table. The header prints the ship-to address, the picking partner and the carrier when `delivery` is installed.
- **Barcode List** — `_wms_barcode_rows()` collects every distinct package barcode and every distinct product barcode of the shipment (falling back to `default_code` when a product has no barcode), de-duplicates them, and renders each as QR *and* Code128 side by side with the human-readable value underneath. `_wms_barcode_row_pairs()` chunks the rows into a two-column grid.
- **Price Tag / Product Label** — the operator opens `custom.wms.label.wizard` (menu Inventory → Warehouse Documents → Print Labels, or the contextual *Print Labels* action on `product.product` / `product.template` / `stock.picking`). `qty_source = manual` repeats each selected product `qty_per_product` times; `qty_source = picking_qty` explodes one label per unit of the picking's move-line quantity (or move demand when the transfer is not reserved yet). The total is checked against `ir.config_parameter custom_wms_docs.max_labels` (default 500) — over the cap `action_print()` raises a `UserError` naming the cap instead of truncating. `action_print()` returns the report action with the expanded label list in `data`; `report.custom_wms_docs.report_wms_product_label` turns it into one sticker dict each.

## Key Models
- `stock.picking` (inherited) — hosts every document helper; no new stored fields are added.
- `custom.wms.label.wizard` (TransientModel) — label print job definition and expansion.
- `report.custom_wms_docs.report_wms_product_label` (AbstractModel) — rendering context for the label grid.
- Read-only consumers: `stock.move.line`, `stock.package`, `stock.package.type`, `product.product`.

## Important Fields
- `custom.wms.label.wizard.picking_id` (M2o `stock.picking`) — optional; required by the form when `qty_source == 'picking_qty'`.
- `custom.wms.label.wizard.product_ids` (M2m `product.product`) — the label subject; also acts as a filter when expanding from a picking.
- `custom.wms.label.wizard.qty_source` (Selection `manual` / `picking_qty`) — `picking_qty` = "one label per unit shipped".
- `custom.wms.label.wizard.qty_per_product` (Integer, default 1) — used in `manual` mode only; ≤ 0 is coerced to 1.
- `custom.wms.label.wizard.label_kind` (Selection `price_tag` / `product_label`) — price tag shows `list_price`; product label shows the UoM instead.
- `custom.wms.label.wizard.barcode_kind` (Selection `Code128` / `QR` / `datamatrix`, default `QR`) — symbology passed to `/report/barcode/<Type>/<value>`.
- System parameter `custom_wms_docs.max_labels` (default `500`, constant `MAX_LABELS_DEFAULT`) — hard cap per print job.

## Public Methods
- `stock.picking._wms_pick_lines(self)` → `stock.move.line` recordset, sorted by `(location_id.complete_name.upper(), product default_code, product display_name, id)`.
- `stock.picking._wms_pick_rows(self) -> list[dict]` — walk rows (`seq`, `line`, `location`, `location_name`, `location_qr`, `product`, `default_code`, `lot_name`, `expiry`, `qty`, `uom_name`).
- `stock.picking._wms_pick_totals(self) -> dict` — `line_count`, `total_qty`.
- `stock.picking._wms_package_block(self, package, lines) -> dict` — one packing block.
- `stock.picking._wms_packing_blocks(self) -> list[dict]` — keys `package` (record or `False`), `name`, `package_type`, `package_type_name`, `dims` (length, width, height), `net_weight`, `gross_weight`, `max_weight`, `lines`, `barcode_code128`, `barcode_qr`.
- `stock.picking._wms_packing_totals(self) -> dict` — `package_count`, `block_count`, `net_weight`, `gross_weight`.
- `stock.picking._wms_barcode_rows(self) -> list[dict]` — keys `kind` (`package`/`product`), `label`, `value`, `qr_src`, `code128_src`.
- `stock.picking._wms_barcode_row_pairs(self, per_row=2) -> list[list[dict]]` — grid chunking.
- `stock.picking._wms_barcode_url(self, value, barcode_type='Code128', width=600, height=100, humanreadable=False) -> str` — template-callable proxy over `models/wms_barcode.py::wms_barcode_url`.
- `stock.picking._wms_line_expiry(self, line)` / `_wms_carrier_name(self)` / `_wms_delivery_partner(self)` / `_wms_line_qty(self, line)` — optional-module-safe accessors.
- `custom.wms.label.wizard._max_labels(self) -> int`, `_picking_label_source(self) -> list[tuple]`, `_expand_labels(self) -> list[dict]`, `action_print(self)`.
- `report.custom_wms_docs.report_wms_product_label._get_report_values(docids, data=None)`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_barcode`, `stock`, `product`.
- **Report XML ids:** `action_report_wms_picking_list`, `action_report_wms_packing_list`, `action_report_wms_barcode_list` (all bound to `stock.picking`), `action_report_wms_product_label` (`product.product`, wizard-driven, not bound to the print menu).
- **Wizard actions:** `action_wms_label_wizard` (menu) plus contextual `action_wms_label_wizard_product` / `_product_template` / `_picking`.
- **External calls:** core web route `/report/barcode/<Type>/<value>` for every barcode image; no third-party service.
- **Optional modules probed at runtime:** `product_expiry` (`stock.lot.expiration_date`), `delivery` (`stock.picking.carrier_id`).
- **Cross-vertical:** generic WMS capability; sits next to `custom_wms_cycle_count` and `custom_barcode`.

## Gotchas
- **Self-contained report wrappers only.** The templates deliberately do *not* use `web.html_container` + `web.external_layout`: on this platform that combination triggers an asset-callback HTTPS round-trip that stalls wkhtmltopdf. Every wrapper also contains a `<main>` element — without it `ir_actions_report._prepare_html` raises `IndexError` on print.
- **Odoo 19 renamed `stock.quant.package` → `stock.package`** (table `stock_package`). Package type is `stock.package.type` with `packaging_length` / `width` / `height` / `base_weight` / `max_weight` / `barcode` / `package_use`.
- **`stock.location` has no `posx` / `posy` / `posz` in Odoo 19**, so the walk path is derived from `complete_name` alone. Rename bins to sort correctly (e.g. `A-01-02`), or override `_wms_pick_lines()`.
- **Products without a `default_code` sort last** in the walk path (sentinel `￿`), not first.
- **Barcode values are URL-quoted with `safe=""`** because references contain `/` (`WH/OUT/00007`) and the core route uses a `<path:value>` converter that would otherwise split them.
- **`datamatrix` degrades to Code128** whenever reportlab cannot draw it — the core `ir.actions.report.barcode()` catches `ValueError`/`AttributeError` and retries as Code128. No error surfaces to the user.
- **Gross weight uses `product.weight` as-is** and `quantity_product_uom` when available; there is no UoM→weight-UoM conversion and no per-package `shipping_weight` override.
- **The label cap raises rather than truncates.** Raise `custom_wms_docs.max_labels` deliberately — a 5 000-label PDF will hurt the wkhtmltopdf worker.
- **`report_action` is called with `discard_logo_check=True`** so an admin on a company without `external_report_layout_id` gets the PDF instead of the layout-configurator wizard.
- **Picking list report has a `domain`** restricting the print binding to `picking_type_id.code in ('outgoing', 'internal')`; the template itself renders any picking.
- **`_wms_pick_lines()` reads `move_line_ids` only.** An unconfirmed/unreserved transfer has none and prints an empty sheet; reserve first.

## Out of Scope
- Label stock / media calibration (label size is fixed CSS: 3 per row, ~34 mm tall) — no per-printer layout model.
- ZPL / EPL direct-to-printer output; PDF only.
- GS1-128 / SSCC composition and validation (`stock.package.valid_sscc` is not consulted).
- Shipping-carrier labels and manifests (that is `delivery`'s job).
- Multi-page package hierarchies — nested `parent_package_id` containers are not recursed; only `result_package_id` is grouped.
- Weight-UoM conversion and volumetric / dimensional weight.
- Any write-back to the picking (nothing is stamped when a document is printed).
