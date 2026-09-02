# -*- coding: utf-8 -*-
import base64
import csv
import io

from odoo import _, fields, models
from odoo.exceptions import UserError


class CustomSpreadsheetImportWizard(models.TransientModel):
    _name = "custom.spreadsheet.import.wizard"
    _description = "Import CSV into Spreadsheet Workbook"

    workbook_id = fields.Many2one(
        "custom.spreadsheet.workbook",
        string="Workbook",
        required=True,
    )
    csv_file = fields.Binary(string="CSV File", required=True)
    csv_filename = fields.Char(string="Filename")
    sheet_name = fields.Char(string="Sheet Name", default="Sheet1", required=True)
    delimiter = fields.Char(string="Delimiter", default=",", required=True, size=2)
    has_header = fields.Boolean(string="File has header row", default=True)

    def action_import(self):
        self.ensure_one()
        if not self.csv_file:
            raise UserError(_("Please attach a CSV file."))
        try:
            raw = base64.b64decode(self.csv_file)
        except (ValueError, TypeError) as e:
            raise UserError(_("Unable to decode the uploaded file: %s") % e) from e
        # Try utf-8-sig first to strip BOM, fallback to latin-1
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="replace")

        buf = io.StringIO(text)
        delim = (self.delimiter or ",")[0]
        try:
            reader = csv.reader(buf, delimiter=delim)
            rows = list(reader)
        except csv.Error as e:
            raise UserError(_("Failed to parse CSV: %s") % e) from e

        if not rows:
            raise UserError(_("CSV file is empty."))

        # has_header is purely informational at the data layer — we keep the
        # header row as row 0 so users can see column titles in the grid.
        # If has_header is False we still store all rows verbatim.
        sheet_name = self.sheet_name or "Sheet1"
        self.workbook_id._apply_csv_rows(rows, sheet_name=sheet_name)

        self.workbook_id.message_post(
            body=_(
                "<b>CSV imported</b>: %(n)s row(s) into sheet <i>%(s)s</i> (header=%(h)s, delimiter=<code>%(d)s</code>)"
            )
            % {
                "n": len(rows),
                "s": sheet_name,
                "h": "yes" if self.has_header else "no",
                "d": delim,
            },
            subtype_xmlid="mail.mt_note",
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "custom.spreadsheet.workbook",
            "res_id": self.workbook_id.id,
            "view_mode": "form",
            "target": "current",
        }
