# -*- coding: utf-8 -*-
"""Ingest DJP-assigned bupot numbers via CSV upload.

Expected CSV format (header row required, comma-separated, UTF-8)::

    internal_ref,bupot_number
    BPU0000123,2310000000001
    BPU0000124,2310000000002

Behaviour:

* Lines are matched by ``internal_ref`` (case-sensitive).
* Missing or duplicate refs are surfaced in ``report`` and do NOT abort
  the rest of the upload — operators get a clear delta to chase.
* When every line in the header has a number AND the header is in state
  ``submitted``, the header is promoted to ``accepted``.
"""

from __future__ import annotations

import base64
import csv
import io

from odoo import _, fields, models
from odoo.exceptions import UserError


class CustomBupotNumberUploadWizard(models.TransientModel):
    _name = "custom.bupot.number.upload.wizard"
    _description = "Upload DJP-assigned Bupot Numbers (CSV)"

    bupot_id = fields.Many2one(
        comodel_name="custom.bupot.unifikasi",
        required=True,
    )
    csv_file = fields.Binary(string="CSV File", required=True)
    csv_filename = fields.Char()
    report = fields.Text(readonly=True)

    def action_apply(self):
        self.ensure_one()
        if not self.csv_file:
            raise UserError(_("Please upload a CSV file."))
        raw = base64.b64decode(self.csv_file).decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(raw))
        if not reader.fieldnames or "internal_ref" not in reader.fieldnames or "bupot_number" not in reader.fieldnames:
            raise UserError(_("CSV must contain headers: internal_ref, bupot_number"))
        Line = self.env["custom.bupot.unifikasi.line"]
        matched, missing, ambiguous = 0, [], []
        for row in reader:
            ref = (row.get("internal_ref") or "").strip()
            num = (row.get("bupot_number") or "").strip()
            if not ref or not num:
                continue
            lines = Line.search([("bupot_id", "=", self.bupot_id.id), ("internal_ref", "=", ref)])
            if not lines:
                missing.append(ref)
                continue
            if len(lines) > 1:
                ambiguous.append(ref)
                continue
            lines.bupot_number = num
            matched += 1

        report_lines = [f"Matched: {matched}"]
        if missing:
            report_lines.append("Missing refs: " + ", ".join(missing))
        if ambiguous:
            report_lines.append("Ambiguous refs: " + ", ".join(ambiguous))
        self.report = "\n".join(report_lines)

        # Auto-promote to "accepted" if all lines now have numbers.
        period = self.bupot_id
        all_filled = period.line_ids and all(l.bupot_number for l in period.line_ids)
        if all_filled and period.state == "submitted":
            period.state = "accepted"

        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
