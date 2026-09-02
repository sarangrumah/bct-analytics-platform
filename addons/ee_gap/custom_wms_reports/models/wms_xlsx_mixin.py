# -*- coding: utf-8 -*-
"""``custom.wms.xlsx.report`` — one XLSX engine for every WMS analysis report.

The native list export already produces a spreadsheet, but it cannot carry an
image, so a scanned warehouse document loses its barcodes the moment it leaves
Odoo. This mixin re-implements the export with ``xlsxwriter`` so the workbook
carries the *same* two barcode levels as the PDFs:

* **transaction level** — column A, the document key the row belongs to
  (picking / scrap order / count session / location);
* **line-item level** — column B, keyed on the lot when the line is tracked
  and on the product EAN otherwise.

Both are real barcode images, so the sheet can be printed and scanned: the
document column is Code128 (references contain "/"), the item column is
EAN-13 whenever the payload is one. The table stays flat below a single
header row, which keeps Excel autofilter and pivots usable.

A concrete report only declares its shape:

``_xlsx_title()``            sheet + heading text
``_xlsx_columns()``          list of column specs (see :meth:`_xlsx_columns`)
``_xlsx_doc_barcode(rec)``   payload of the transaction-level barcode
``_xlsx_line_barcode(rec)``  payload of the line-item barcode

Everything else — images, number formats, totals, the attachment and the
download action — is handled here.
"""

from __future__ import annotations

import io
import logging
from base64 import b64encode

from odoo import _, models
from odoo.exceptions import UserError

from odoo.addons.custom_wms_docs.models.wms_barcode import wms_barcode_png

_logger = logging.getLogger(__name__)

try:
    import xlsxwriter
except ImportError:  # pragma: no cover - the platform image ships xlsxwriter
    xlsxwriter = None

#: Geometry of the embedded barcode images, in px as handed to reportlab.
BARCODE_SIZE = (330, 48)
#: Row height / column width that fit those images without clipping.
ROW_HEIGHT = 30
BARCODE_COL_WIDTH = 24


class WmsXlsxReport(models.AbstractModel):
    _name = "custom.wms.xlsx.report"
    _description = "WMS Report XLSX Export (with barcodes)"

    # ------------------------------------------------------------------
    # Hooks — overridden by the concrete reports
    # ------------------------------------------------------------------
    def _xlsx_title(self) -> str:
        """Human title of the report; also the sheet name (truncated to 31)."""
        return self._description or self._name

    def _xlsx_columns(self) -> list[dict]:
        """Column specs, left to right, after the two barcode columns.

        Each spec is ``{"label": str, "value": callable(rec) -> value,
        "width": int, "type": "text"|"number"|"money"|"date"|"datetime",
        "total": bool}``. ``total`` sums the column in the footer row.
        """
        raise NotImplementedError

    def _xlsx_doc_barcode(self, rec) -> str:
        """Payload of the transaction-level barcode; ``""`` renders no image."""
        for field in ("reference", "name"):
            value = getattr(rec, field, False)
            if value:
                return value
        picking = getattr(rec, "picking_id", False)
        return picking.name if picking else ""

    def _xlsx_line_barcode(self, rec) -> str:
        """Payload of the line-item barcode; ``""`` renders no image."""
        lot = getattr(rec, "lot_id", False)
        if lot and lot.name:
            return lot.name
        product = getattr(rec, "product_id", False)
        if product:
            return product.barcode or product.default_code or ""
        return ""

    def _xlsx_records(self):
        """Records to export — the selection when there is one, else the whole report."""
        return self if self else self.search([])

    # ------------------------------------------------------------------
    # Engine
    # ------------------------------------------------------------------
    def action_export_xlsx(self):
        """Build the workbook and return a download action for it."""
        if xlsxwriter is None:
            raise UserError(_("The xlsxwriter Python package is not installed on this server."))
        records = self._xlsx_records()
        if not records:
            raise UserError(_("There is nothing to export for this report."))

        data = self._build_xlsx(records)
        filename = "%s.xlsx" % self._xlsx_title().replace("/", "-")
        attachment = self.env["ir.attachment"].create(
            {
                "name": filename,
                "type": "binary",
                "datas": b64encode(data),
                "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                # Not tied to a record: an _auto=False view has no storage of
                # its own, and the file is a throwaway download anyway.
                "res_model": False,
                "res_id": False,
            }
        )
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attachment.id,
            "target": "self",
        }

    def _build_xlsx(self, records) -> bytes:
        columns = self._xlsx_columns()
        stream = io.BytesIO()
        book = xlsxwriter.Workbook(stream, {"in_memory": True, "remove_timezone": True})
        fmt = self._xlsx_formats(book)
        sheet = book.add_worksheet(self._xlsx_title()[:31])

        # Columns A and B carry the two barcode levels.
        sheet.set_column(0, 1, BARCODE_COL_WIDTH)
        for idx, col in enumerate(columns, start=2):
            sheet.set_column(idx, idx, col.get("width", 18))
        last_col = len(columns) + 1

        sheet.merge_range(0, 0, 0, last_col, self._xlsx_title(), fmt["title"])
        sheet.set_row(0, 22)

        header_row = 2
        sheet.write(header_row, 0, _("Document Barcode"), fmt["header"])
        sheet.write(header_row, 1, _("Item Barcode"), fmt["header"])
        for idx, col in enumerate(columns):
            sheet.write(header_row, idx + 2, col["label"], fmt["header"])
        sheet.freeze_panes(header_row + 1, 2)

        totals = {i: 0.0 for i, col in enumerate(columns) if col.get("total")}
        row = header_row + 1

        for rec in records:
            sheet.set_row(row, ROW_HEIGHT)
            self._write_barcode(sheet, row, 0, self._xlsx_doc_barcode(rec), "Code128")
            # "auto" renders a 13-digit EAN as EAN-13: handhelds are routinely
            # shipped with Code128 decoding switched off.
            self._write_barcode(sheet, row, 1, self._xlsx_line_barcode(rec), "auto")
            for idx, col in enumerate(columns):
                value = col["value"](rec)
                col_type = col.get("type", "text")
                cell_fmt = fmt.get(col_type, fmt["text"])
                if col_type in ("number", "money"):
                    value = float(value or 0.0)
                    if idx in totals:
                        totals[idx] += value
                    sheet.write_number(row, idx + 2, value, cell_fmt)
                elif col_type in ("date", "datetime") and value:
                    sheet.write_datetime(row, idx + 2, value, cell_fmt)
                else:
                    sheet.write(row, idx + 2, "" if value in (False, None) else str(value), cell_fmt)
            row += 1

        if totals:
            sheet.write(row, 0, _("TOTAL"), fmt["total_text"])
            sheet.write(row, 1, "", fmt["total_text"])
            for idx, _col in enumerate(columns):
                if idx in totals:
                    sheet.write_number(row, idx + 2, totals[idx], fmt["total_num"])
                else:
                    sheet.write(row, idx + 2, "", fmt["total_text"])

        # Autofilter over the flat table (skipped when there is no data row).
        if row > header_row + 1:
            sheet.autofilter(header_row, 0, row - 1, last_col)

        book.close()
        return stream.getvalue()

    # ------------------------------------------------------------------
    # Building blocks
    # ------------------------------------------------------------------
    def _write_barcode(self, sheet, row, col, value, symbology="Code128") -> None:
        """Insert a barcode image of ``value`` into a cell (no-op if empty)."""
        if not value:
            return
        png = wms_barcode_png(self.env, value, symbology, BARCODE_SIZE[0], BARCODE_SIZE[1], humanreadable=True)
        if not png and symbology != "Code128":
            # EAN rendering rejected the payload (wrong length / checksum).
            png = wms_barcode_png(self.env, value, "Code128", BARCODE_SIZE[0], BARCODE_SIZE[1], humanreadable=True)
        if not png:
            return
        sheet.insert_image(
            row,
            col,
            "%s.png" % value,
            {
                "image_data": io.BytesIO(png),
                "x_scale": 0.5,
                "y_scale": 0.5,
                "x_offset": 3,
                "y_offset": 2,
                # 2 = move but don't size with cells: a resized column must not
                # stretch the bars out of spec.
                "object_position": 2,
            },
        )

    def _xlsx_formats(self, book) -> dict:
        return {
            "title": book.add_format({"bold": True, "font_size": 13, "align": "left", "valign": "vcenter"}),
            "header": book.add_format(
                {
                    "bold": True,
                    "bg_color": "#F0F0F0",
                    "border": 1,
                    "align": "center",
                    "valign": "vcenter",
                    "text_wrap": True,
                }
            ),
            "text": book.add_format({"border": 1, "valign": "vcenter"}),
            "number": book.add_format({"border": 1, "valign": "vcenter", "num_format": "#,##0.000"}),
            "money": book.add_format({"border": 1, "valign": "vcenter", "num_format": "#,##0.00"}),
            "date": book.add_format({"border": 1, "valign": "vcenter", "num_format": "yyyy-mm-dd"}),
            "datetime": book.add_format({"border": 1, "valign": "vcenter", "num_format": "yyyy-mm-dd hh:mm"}),
            "total_text": book.add_format({"bold": True, "bg_color": "#E8E8E8", "border": 1}),
            "total_num": book.add_format({"bold": True, "bg_color": "#E8E8E8", "border": 1, "num_format": "#,##0.00"}),
        }
