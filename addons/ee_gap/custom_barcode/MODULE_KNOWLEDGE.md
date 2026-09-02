---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_barcode
manifest_version: 19.0.2.0.0
---

# custom_barcode

## Purpose
CE-compatible replacement for the EE-only `stock_barcode` app. Builds on the CE `barcodes` + `barcodes_gs1_nomenclature` modules to provide a mobile/kiosk-friendly scan workflow that **actually mutates `stock.move.line`** on a picking — including real GS1 AI parsing (GTIN, lot, expiry, weight), batch picking (one scan distributed across many pickings), cluster picking (one walk for many orders grouped by source location), barcode-format auto-generation, label templates (ZPL/ESC-POS/PDF), printer configuration, and a print spool.

## Business Flow
- **Single picking flow**: operator opens a `custom.barcode.scan.session` linked to a `stock.picking`, calls `action_start_scanning`, scans products via `on_barcode_scanned(barcode)`. Each scan parses GS1, looks up product (by GTIN then raw barcode) + lot, creates a `custom.barcode.scan.line` with status `ok`/`not_found`/`duplicate`. `action_apply_to_picking` reconciles OK lines against `stock.move.line.qty_done` (creating lots and move.lines as needed), then posts a chatter summary on both session and picking.
- **Batch flow**: `custom.barcode.batch.session` aggregates scans across many pickings without pre-allocation (status `unallocated`). `auto_distribute_lines()` walks pickings in order, greedy-fills each picking's outstanding demand per product, splits scan lines when they span pickings, then `action_apply()` reuses the standard session apply per picking.
- **Cluster flow**: `custom.barcode.cluster.run` calls `build_plan()` which groups outstanding moves by `(location, product, picking)`, sorts by `location.complete_name → product → picking name` for a walk-order pick. Each scan increments the matching `custom.barcode.cluster.assignment.scanned_qty`; `action_apply()` materialises sessions per picking.
- **Auto-barcoding**: `custom.barcode.format` defines `code` (Code128/EAN-13/EAN-8/QR) + `prefix` + `suffix` + `sequence_id` + `applied_models`. `product.product.create` and `stock.lot.create` look up `_format_for_model` and auto-populate `barcode` / `name` (EAN check-digit computed in-place).
- **Labels + Printing**: `custom.label.template` renders `{{field}}` / `{{rel.field}}` substitutions to ZPL/ESC-POS/PDF bytes. `custom.printer.config` supports `zebra_network` / `zebra_usb` / `escpos_network` / `cups` transports — network printers go via raw socket 9100, CUPS is stubbed (no python-cups dependency). `custom.print.queue` spools jobs with state queued/printing/done/failed; cron `_cron_process_queue` drains 50/tick.
- **Reporting**: `stock.picking._barcode_summary_rows()` and `custom.barcode.scan.session.get_picking_summary_data()` feed a QWeb-PDF `picking_barcode_summary` report with expected vs scanned + deviation %.

## Key Models
- `custom.barcode.scan.session` — Single-picking scan session; inherits `barcodes.barcode_events_mixin` for HW event capture.
- `custom.barcode.scan.line` — One scan event; belongs to a session, batch, or cluster (constrained to exactly one owner).
- `custom.barcode.batch.session` — Scan-many-pickings session with greedy distributor.
- `custom.barcode.cluster.run` + `custom.barcode.cluster.assignment` — One operator, many orders, grouped by location.
- `custom.barcode.format` + `custom.barcode.auto.mixin` — Auto-barcoding on product/lot create.
- `custom.label.template` — Renderable ZPL/ESC-POS/PDF label.
- `custom.printer.config` — Physical/virtual printer with raw-socket or CUPS transport.
- `custom.print.queue` — Async print-job spool.
- `stock.picking` (inherited) — `_barcode_summary_sessions` + `_barcode_summary_rows` for the QWeb report.

## Important Fields
- `custom.barcode.scan.session.state` (draft/scanning/completed/cancelled).
- `custom.barcode.scan.line.status` (ok/not_found/duplicate/wrong_location/unallocated) — `_check_owner` constraint enforces exactly one of session/batch/cluster.
- `custom.barcode.scan.line.x_gs1_parsed` (Text, JSON) — parsed GS1 AI dict (gtin/lot/exp_date/prod_date/serial/weight/weight_unit/count).
- `custom.barcode.scan.line.quantity` (Float, default 1.0) — overridden by GS1 weight when present.
- `custom.barcode.batch.session.picking_ids` (M2m `stock.picking`) — pickings the batch can drain into; domain `state in ('confirmed','assigned')`.
- `custom.barcode.cluster.assignment.expected_qty` / `scanned_qty` / `remaining_qty` (computed) — per-stop progress.
- `custom.barcode.format.code` (Selection: Code128/EAN13/EAN8/QR) — auto-applies EAN check-digit via `_ensure_ean13` / `_ensure_ean8`.
- `custom.barcode.format.applied_models` (M2m `ir.model`, restricted to product.product/product.template/stock.lot/stock.location).
- `custom.label.template.output_mode` (zpl/escpos/pdf), `paper_format`, `template_source` (placeholder body).
- `custom.printer.config.printer_type` (zebra_network/zebra_usb/escpos_network/cups), `host`, `port` (default 9100), `cups_queue`, `last_error`.
- `custom.print.queue.state` (queued/printing/done/failed), `res_model` + `res_ids` (CSV) + `copies`.

## Public Methods
- `custom.barcode.scan.session.parse_gs1(barcode)` (`@api.model`) — Subset GS1 AI parser: AI 01/10/17/11/21/30 + 310n/320n weight.
- `custom.barcode.scan.session.on_barcode_scanned(barcode)` — HW event handler; creates scan line with GS1 enrichment.
- `custom.barcode.scan.session.action_apply_to_picking()` — Reconcile OK lines onto `stock.move.line.qty_done`, create lots + move.lines as needed.
- `custom.barcode.scan.session.action_open_kiosk()` — Fullscreen kiosk form.
- `custom.barcode.scan.session.get_picking_summary_data()` — QWeb report data source.
- `custom.barcode.batch.session.auto_distribute_lines()` — Greedy first-fit per picking; splits lines as needed.
- `custom.barcode.batch.session.action_apply()` — Materialise per-picking sessions and apply.
- `custom.barcode.cluster.run.build_plan()` — Generate sorted pick assignments.
- `custom.barcode.cluster.run.action_apply()` — Apply per-picking after walk.
- `custom.barcode.format.generate()` — Next barcode for the format (sequence + prefix/suffix + EAN check-digit).
- `custom.barcode.auto.mixin._custom_barcode_autogenerate(vals_list, field=...)` — `create()`-hook helper.
- `custom.label.template.render(record, qty=1)` — Render placeholders to encoded bytes.
- `custom.printer.config.send_raw(payload)` — Dispatch over configured transport.
- `custom.print.queue.action_process()` / `_cron_process_queue()` — Synchronous render+send / cron drain.
- `stock.picking._barcode_summary_rows()` — Expected vs scanned per product + deviation %.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `barcodes`, `barcodes_gs1_nomenclature`, `stock`.
- **Inherits from:** `barcodes.barcode_events_mixin` (scan session, batch, cluster), `mail.thread` + `pdp.audited.mixin` (sessions), `product.product` + `stock.lot` (`create` hook for auto-barcoding), `stock.picking` (report helpers).
- **Extended by:** `custom_wms_putaway` (depends on this for HHT scan flow), `custom_wms_cycle_count` (HHT counting), `custom_hht_bridge` (rugged-device scan integration).
- **External calls:** raw TCP socket to printers on port 9100 (`socket.create_connection`); CUPS is a logging-only stub.
- **Cross-vertical:** generic inventory barcode capability shared by all warehouse-bearing verticals.

## Gotchas
- **GS1 parser only supports a subset of AIs** — 01 (GTIN), 10 (lot, FNC1-terminated), 17 (exp YYMMDD), 11 (prod date), 21 (serial), 30 (count), 310n / 320n (weight kg/lb). Unknown AIs cause the parser to stop at that point and return what it has so far.
- **`apply_to_picking` field name detection (`qty_done` vs `quantity`)** is version-fragile — a runtime `'qty_done' in MoveLine._fields` probe picks the name, and `reserved_uom_qty` vs `quantity_product_uom` is probed the same way. Both names are now resolved **once** at the top of the reconciliation loop. Previously the probe existed in one place while two other spots hardcoded `qty_done`, so on Odoo 19 (where the field is `quantity`) `action_apply_to_picking` raised `ValueError: Invalid field 'qty_done' in 'stock.move.line'` — the entire scan-apply path was dead. Resolve the names once; never inline the literal.
- **`auto_distribute_lines` is first-fit by `picking_ids` order** — picking order in the M2m matters; no smarter optimisation (e.g. nearest deadline).
- **`action_apply` (batch/cluster) reparents lines into a transient session, runs apply, then detaches** — if apply raises mid-flight, lines may be orphaned to a half-applied session.
- **`custom.barcode.format._format_for_model` returns the first match by `(sequence, id)`** — multiple active formats on the same model silently lose precedence to whichever sorts first.
- **EAN check-digit computation ignores non-digit characters** in the raw input — prefix/suffix can yield short digit pools and unexpected padding.
- **`custom.printer.config.send_raw` for `zebra_usb` raises `UserError`** — USB requires an out-of-process local agent that is not shipped here.
- **`custom.print.queue.res_ids` is a CSV Text field, not Json/Many2many** — handcrafted parsing; values must be plain digits.
- **`_check_owner` constraint allows zero ownership only if all three FKs are False** — `ValidationError` on save. Code paths must always set at least one owner.
- **GS1 weight defaults to `1.0` quantity** when no weight AI is present — silently treats every non-weight scan as one unit.

## Out of Scope
- Pre-bundled mobile/PWA frontend — relies on Odoo web client with kiosk view.
- USB/Bluetooth printer support without an external local agent.
- python-cups direct integration (stub only).
- Full GS1 nomenclature coverage (delegated to `barcodes_gs1_nomenclature` for non-parsed AIs).
- Per-line wrong-location detection — `wrong_location` status is defined but never set by the standard flow.
