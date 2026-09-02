# -*- coding: utf-8 -*-
"""Witholding rate matrix."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


PPH_TYPES = [
    ("23", "PPh Pasal 23"),
    ("22", "PPh Pasal 22"),
    ("4_2", "PPh Pasal 4 ayat (2)"),
    ("15", "PPh Pasal 15"),
    ("21", "PPh Pasal 21"),
    ("26", "PPh Pasal 26"),
]


class CustomWitholdingRate(models.Model):
    _name = "custom.witholding.rate"
    _inherit = ["pdp.audited.mixin"]
    _description = "PPh Witholding Rate"
    _order = "effective_date_from desc, pph_type, service_category"

    name = fields.Char(compute="_compute_name", store=True)
    pph_type = fields.Selection(selection=PPH_TYPES, required=True)
    service_category = fields.Char(
        required=True,
        default="general",
        help="Free-text category, e.g. 'sewa', 'jasa_teknik', "
        "'manajemen', 'general'. Used as a discriminator when "
        "multiple rates apply to the same pph_type on the same date.",
    )
    with_npwp_rate = fields.Float(
        string="Rate w/ NPWP (%)",
        digits=(6, 4),
        required=True,
        help="Effective rate when partner has a valid NPWP.",
    )
    without_npwp_rate = fields.Float(
        string="Rate w/o NPWP (%)",
        digits=(6, 4),
        required=True,
        help="Punitive rate (typically 2× the with-NPWP rate per UU PPh Pasal 23 ayat (1a)).",
    )
    effective_date_from = fields.Date(required=True)
    effective_date_to = fields.Date(
        help="Leave empty for open-ended validity.",
    )
    legal_basis = fields.Text(
        help="Citation of the underlying regulation, e.g. 'UU PPh Pasal 23 ayat (1) huruf c angka 2'.",
    )
    active = fields.Boolean(default=True)

    @api.depends("pph_type", "service_category", "with_npwp_rate", "effective_date_from")
    def _compute_name(self):
        for rec in self:
            rec.name = (
                f"PPh {rec.pph_type or '?'} / {rec.service_category or '?'} "
                f"@ {rec.with_npwp_rate:.2f}% "
                f"(from {rec.effective_date_from or '?'})"
            )

    @api.constrains("with_npwp_rate", "without_npwp_rate")
    def _check_rates(self):
        for rec in self:
            for r in (rec.with_npwp_rate, rec.without_npwp_rate):
                if r < 0 or r > 100:
                    raise ValidationError(_("Witholding rates must be between 0 and 100%."))

    @api.constrains("effective_date_from", "effective_date_to")
    def _check_dates(self):
        for rec in self:
            if rec.effective_date_to and rec.effective_date_to < rec.effective_date_from:
                raise ValidationError(_("effective_date_to must be on or after effective_date_from."))

    @api.model
    def _find_active(self, pph_type: str, service_category: str | None, date):
        """Return the most-specific active rate or empty recordset."""
        domain = [
            ("active", "=", True),
            ("pph_type", "=", pph_type),
            ("effective_date_from", "<=", date),
            "|",
            ("effective_date_to", "=", False),
            ("effective_date_to", ">=", date),
        ]
        if service_category:
            domain += [("service_category", "=", service_category)]
        rec = self.search(domain, order="effective_date_from desc", limit=1)
        if rec or not service_category:
            return rec
        # Fallback: try 'general' if specific category missing.
        return self.search(
            [
                ("active", "=", True),
                ("pph_type", "=", pph_type),
                ("service_category", "=", "general"),
                ("effective_date_from", "<=", date),
                "|",
                ("effective_date_to", "=", False),
                ("effective_date_to", ">=", date),
            ],
            order="effective_date_from desc",
            limit=1,
        )
