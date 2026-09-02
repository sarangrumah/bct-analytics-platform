# -*- coding: utf-8 -*-
"""Bulk pre-export validation: NPWP/NIK/DPP/sertel checks for a batch of moves.

Run this before opening the Coretax export wizard to catch validation
failures up-front rather than per-XML at upload time.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from odoo import fields, models

_logger = logging.getLogger(__name__)


class BulkValidationWizard(models.TransientModel):
    _name = "tax.bulk.validation.wizard"
    _description = "Wizard: bulk pre-export validation"

    date_from = fields.Date(required=True, default=lambda self: date.today().replace(day=1))
    date_to = fields.Date(required=True, default=fields.Date.context_today)
    move_type = fields.Selection(
        [
            ("out_invoice", "Faktur Keluaran"),
            ("in_invoice", "Faktur Masukan"),
            ("both", "Keduanya"),
        ],
        default="out_invoice",
        required=True,
    )
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)

    result_count_total = fields.Integer(readonly=True)
    result_count_invalid = fields.Integer(readonly=True)
    result_html = fields.Html(readonly=True, sanitize=False)
    run_done = fields.Boolean(readonly=True)

    # ------------------------------------------------------------------

    def action_run(self):
        self.ensure_one()
        move_types = (
            ("out_invoice", "out_refund")
            if self.move_type == "out_invoice"
            else ("in_invoice", "in_refund")
            if self.move_type == "in_invoice"
            else ("out_invoice", "out_refund", "in_invoice", "in_refund")
        )
        moves = (
            self.env["account.move"]
            .sudo()
            .search(
                [
                    ("company_id", "=", self.company_id.id),
                    ("state", "=", "posted"),
                    ("move_type", "in", move_types),
                    ("invoice_date", ">=", self.date_from),
                    ("invoice_date", "<=", self.date_to),
                ]
            )
        )

        errors: list[dict[str, Any]] = []
        sertel_warning = self._check_sertel(self.company_id)

        for move in moves:
            issues = self._check_move(move)
            if issues:
                errors.append({"move": move, "issues": issues})

        # Build HTML report
        html_parts = []
        if sertel_warning:
            html_parts.append(f'<div class="alert alert-warning">{sertel_warning}</div>')
        if not errors:
            html_parts.append(
                f'<div class="alert alert-success"><b>✓ All {len(moves)} moves pass validation.</b></div>'
            )
        else:
            html_parts.append(
                f'<div class="alert alert-danger"><b>{len(errors)} of {len(moves)} moves have issues</b></div>'
            )
            html_parts.append('<table class="table table-sm">')
            html_parts.append(
                "<thead><tr><th>Move</th><th>Partner</th><th>Date</th><th>Issues</th></tr></thead><tbody>"
            )
            for row in errors:
                m = row["move"]
                issue_html = "<br/>".join(f"• {i}" for i in row["issues"])
                html_parts.append(
                    f"<tr><td>{m.name or m.id}</td>"
                    f"<td>{m.partner_id.name or ''}</td>"
                    f"<td>{m.invoice_date or ''}</td>"
                    f"<td>{issue_html}</td></tr>"
                )
            html_parts.append("</tbody></table>")

        self.write(
            {
                "result_count_total": len(moves),
                "result_count_invalid": len(errors),
                "result_html": "".join(html_parts),
                "run_done": True,
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    # ------------------------------------------------------------------

    def _check_move(self, move) -> list[str]:
        issues: list[str] = []
        partner = move.partner_id.commercial_partner_id

        npwp_status = getattr(partner, "x_custom_npwp_status", None)
        if move.move_type in ("out_invoice", "out_refund"):
            # Outbound: customer NPWP optional but tracked
            if npwp_status == "invalid":
                issues.append(f"Partner NPWP '{partner.x_custom_npwp}' invalid (must be 15 or 16 digits).")
        else:
            # Inbound (vendor bill): vendor NPWP needed for PPh withholding documentation
            if npwp_status == "none":
                issues.append("Vendor tidak punya NPWP — PPh akan dipotong dengan tarif tanpa-NPWP.")
            elif npwp_status == "invalid":
                issues.append(f"Vendor NPWP '{partner.x_custom_npwp}' invalid.")

        # DPP > 0
        if move.amount_untaxed <= 0:
            issues.append(f"DPP must be > 0 (current: {move.amount_untaxed}).")

        # NIK check for individual partners (orang pribadi)
        if not partner.is_company and not getattr(partner, "x_custom_nik", None):
            issues.append("Partner orang pribadi tanpa NIK — wajib untuk Bupot PPh 21/23/26.")

        return issues

    def _check_sertel(self, company) -> str | None:
        """Sertel attached + not expired? Returns warning HTML or None."""
        Config = self.env.get("custom.coretax.config")
        if Config is None:
            return None
        cfg = Config.sudo().search([("company_id", "=", company.id)], limit=1)
        if not cfg:
            return "Coretax config tidak ditemukan untuk perusahaan ini — sertel belum diatur."
        if not getattr(cfg, "sertel_attachment_id", False):
            return "Sertifikat Elektronik (sertel) belum diupload di Coretax config."
        expiry = getattr(cfg, "sertel_expiry_date", None)
        if expiry and expiry < date.today():
            return f"Sertel kadaluarsa pada {expiry} — perlu perpanjangan sebelum submit Faktur."
        return None
