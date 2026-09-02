# -*- coding: utf-8 -*-
"""Receipt template import — bulk load serial/lot/expiry/supplier-batch.

Upload a CSV or XLSX with one row per serial (qty 1) or per lot (qty n).
Recognised headers (case-insensitive, first match wins):

===================  =========================================
column               aliases
===================  =========================================
barcode              barcode, ean, gtin, sku, default_code
serial               serial, imei, serial_number
lot                  lot, batch, lot_number
qty                  qty, quantity
expiry               expiry, expiry_date, expiration, exp_date
supplier_batch       supplier_batch, supplier batch, vendor_batch
===================  =========================================

All-or-nothing: any bad row aborts the import with a per-row error list so
the file can be fixed and re-uploaded.
"""

from __future__ import annotations

import base64
import csv
import io
from datetime import date, datetime

from odoo import _, fields, models
from odoo.exceptions import UserError

HEADER_ALIASES = {
    "barcode": {"barcode", "ean", "gtin", "sku", "default_code"},
    "serial": {"serial", "imei", "serial_number"},
    "lot": {"lot", "batch", "lot_number"},
    "qty": {"qty", "quantity"},
    "expiry": {"expiry", "expiry_date", "expiration", "exp_date"},
    "supplier_batch": {"supplier_batch", "supplier batch", "vendor_batch"},
}

TEMPLATE_CSV = "barcode,serial,lot,qty,expiry,supplier_batch\r\n"

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y")


class WmsReceiptImportWizard(models.TransientModel):
    _name = "custom.wms.receipt.import.wizard"
    _description = "Receipt Template Import"

    picking_id = fields.Many2one(
        "stock.picking",
        required=True,
        default=lambda self: self.env.context.get("active_id"),
        domain=[("state", "not in", ("done", "cancel"))],
    )
    data_file = fields.Binary(string="Template File", required=True)
    data_file_name = fields.Char(string="File Name")

    # ------------------------------------------------------------------
    # Template download
    # ------------------------------------------------------------------
    def action_download_template(self):
        attachment = self.env["ir.attachment"].create(
            {
                "name": "receipt_import_template.csv",
                "type": "binary",
                "datas": base64.b64encode(TEMPLATE_CSV.encode("utf-8")),
                "mimetype": "text/csv",
                "res_model": self._name,
                "res_id": 0,
            }
        )
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    def _read_rows(self):
        """Return list of dicts keyed by canonical header names."""
        self.ensure_one()
        raw = base64.b64decode(self.data_file or b"")
        name = (self.data_file_name or "").lower()
        if name.endswith(".xlsx"):
            rows = self._rows_from_xlsx(raw)
        else:
            rows = self._rows_from_csv(raw)
        if not rows:
            raise UserError(_("The file has no data rows."))
        header, data_rows = rows[0], rows[1:]
        colmap = {}
        for idx, cell in enumerate(header):
            key = str(cell or "").strip().lower()
            for canonical, aliases in HEADER_ALIASES.items():
                if key in aliases and canonical not in colmap:
                    colmap[canonical] = idx
        if "barcode" not in colmap:
            raise UserError(_("Missing a product column: the header must contain one of barcode / ean / gtin / sku."))
        records = []
        for line_no, row in enumerate(data_rows, start=2):
            if not any(str(c or "").strip() for c in row):
                continue

            def cell(key, row=row):
                idx = colmap.get(key)
                if idx is None or idx >= len(row):
                    return ""
                value = row[idx]
                if value is None:
                    return ""
                if isinstance(value, (datetime, date)):
                    return value
                return str(value).strip()

            records.append(
                {
                    "line_no": line_no,
                    "barcode": cell("barcode"),
                    "serial": cell("serial"),
                    "lot": cell("lot"),
                    "qty": cell("qty"),
                    "expiry": cell("expiry"),
                    "supplier_batch": cell("supplier_batch"),
                }
            )
        return records

    @staticmethod
    def _rows_from_csv(raw):
        text = raw.decode("utf-8-sig", errors="replace")
        return [row for row in csv.reader(io.StringIO(text))]

    @staticmethod
    def _rows_from_xlsx(raw):
        try:
            import openpyxl
        except ImportError as exc:  # pragma: no cover
            raise UserError(_("openpyxl is not available on the server; upload CSV instead.")) from exc
        workbook = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        sheet = workbook.active
        return [[cell for cell in row] for row in sheet.iter_rows(values_only=True)]

    @staticmethod
    def _parse_date(value, line_no, errors):
        if not value:
            return False
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        errors.append(_("Row %(row)s: cannot parse date %(val)r.", row=line_no, val=value))
        return False

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------
    def action_import(self):
        self.ensure_one()
        picking = self.picking_id
        records = self._read_rows()
        Product = self.env["product.product"]
        Lot = self.env["stock.lot"]
        MoveLine = self.env["stock.move.line"]
        qty_field = "qty_done" if "qty_done" in MoveLine._fields else "quantity"

        errors = []
        plan = []
        for rec in records:
            product = Product._resolve_barcode(rec["barcode"])
            if not product:
                product = Product.search([("default_code", "=", rec["barcode"])], limit=1)
            if not product:
                errors.append(
                    _("Row %(row)s: no product for barcode/SKU %(code)r.", row=rec["line_no"], code=rec["barcode"])
                )
                continue
            if not picking.move_ids.filtered(lambda m, p=product: m.product_id == p):
                errors.append(
                    _(
                        "Row %(row)s: product %(prod)s has no demand line on %(pick)s.",
                        row=rec["line_no"],
                        prod=product.display_name,
                        pick=picking.name,
                    )
                )
                continue

            qty = 1.0
            if rec["qty"]:
                try:
                    qty = float(str(rec["qty"]).replace(",", "."))
                except ValueError:
                    errors.append(_("Row %(row)s: invalid quantity %(val)r.", row=rec["line_no"], val=rec["qty"]))
                    continue
            lot_name = rec["serial"] or rec["lot"]
            if rec["serial"]:
                if qty != 1.0:
                    errors.append(_("Row %(row)s: a serial/IMEI row must have qty 1.", row=rec["line_no"]))
                    continue
                if product.tracking == "none":
                    errors.append(
                        _(
                            "Row %(row)s: %(prod)s is not tracked, but a serial was given.",
                            row=rec["line_no"],
                            prod=product.display_name,
                        )
                    )
                    continue
            if product.tracking != "none" and not lot_name:
                errors.append(
                    _(
                        "Row %(row)s: %(prod)s is tracked by %(track)s — a serial/lot is required.",
                        row=rec["line_no"],
                        prod=product.display_name,
                        track=product.tracking,
                    )
                )
                continue
            expiry = self._parse_date(rec["expiry"], rec["line_no"], errors)
            plan.append(
                {
                    "product": product,
                    "lot_name": lot_name,
                    "qty": qty,
                    "expiry": expiry,
                    "supplier_batch": rec["supplier_batch"],
                }
            )

        if errors:
            raise UserError(_("Import aborted, fix the file first:\n\n%s") % "\n".join(errors))

        # The template is authoritative for the products it lists: zero the
        # pre-filled (auto-generated) quantities of those products first, so
        # imported rows SET quantities instead of stacking on the defaults
        # Odoo writes on incoming move lines at confirmation.
        products = {item["product"].id for item in plan}
        picking.move_line_ids.filtered(lambda ml: ml.product_id.id in products).write({qty_field: 0.0})

        applied = 0
        created_lots = 0
        for item in plan:
            product = item["product"]
            lot = False
            if item["lot_name"] and product.tracking != "none":
                lot = Lot.search(
                    [("name", "=", item["lot_name"]), ("product_id", "=", product.id)],
                    limit=1,
                )
                if not lot:
                    lot = Lot.create(
                        {
                            "name": item["lot_name"],
                            "product_id": product.id,
                            "company_id": picking.company_id.id,
                        }
                    )
                    created_lots += 1
                vals = {}
                if item["expiry"]:
                    # Explicit template date overrides the auto default that
                    # product_expiry stamps on newly created lots.
                    vals["expiration_date"] = fields.Datetime.to_datetime(item["expiry"])
                if item["supplier_batch"] and not lot.supplier_batch_ref:
                    vals["supplier_batch_ref"] = item["supplier_batch"]
                if vals:
                    lot.write(vals)

            domain = [
                ("picking_id", "=", picking.id),
                ("product_id", "=", product.id),
                ("lot_id", "in", (False, lot.id) if lot else (False,)),
            ]
            ml = MoveLine.search(domain, limit=1)
            if not ml:
                move = picking.move_ids.filtered(lambda m, p=product: m.product_id == p)[:1]
                ml = MoveLine.create(
                    {
                        "move_id": move.id,
                        "picking_id": picking.id,
                        "product_id": product.id,
                        "product_uom_id": move.product_uom.id,
                        "location_id": move.location_id.id,
                        "location_dest_id": move.location_dest_id.id,
                        qty_field: 0.0,
                        "company_id": picking.company_id.id,
                    }
                )
            if lot and not ml.lot_id:
                ml.lot_id = lot.id
            elif lot and ml.lot_id != lot:
                # Same product, different lot: new dedicated line.
                ml = ml.copy({qty_field: 0.0, "lot_id": lot.id})
            ml[qty_field] = (ml[qty_field] or 0.0) + item["qty"]
            applied += 1

        picking.message_post(
            body=_(
                "<b>Receipt template imported</b><br/>File: %(file)s<br/>"
                "Rows applied: %(rows)s | Lots created: %(lots)s",
                file=self.data_file_name or "-",
                rows=applied,
                lots=created_lots,
            ),
            subtype_xmlid="mail.mt_note",
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Receipt import done"),
                "message": _("%(rows)s row(s) applied, %(lots)s lot(s) created.", rows=applied, lots=created_lots),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
