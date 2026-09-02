# -*- coding: utf-8 -*-
"""TER (Tarif Efektif Rata-rata) table — PP 58/2023, effective Jan 2024.

PP 58/2023 redefined monthly PPh 21 withholding for "Pegawai Tetap" into
three categories — A, B, C — each with its own bracketed table indexed
by monthly *gross* income. Year-end reconciliation still uses the
progressive UU HPP brackets; TER is purely the monthly proxy.

Category mapping (PPh 21 ART. 23A):
  A: TK/0, TK/1, K/0
  B: TK/2, TK/3, K/1, K/2
  C: K/3
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


TER_CATEGORY_SELECTION = [
    ("A", "Kategori A (TK/0, TK/1, K/0)"),
    ("B", "Kategori B (TK/2, TK/3, K/1, K/2)"),
    ("C", "Kategori C (K/3)"),
]

# Mapping from PTKP code → TER category
PTKP_TO_TER_CATEGORY = {
    "TK/0": "A",
    "TK/1": "A",
    "K/0": "A",
    "TK/2": "B",
    "TK/3": "B",
    "K/1": "B",
    "K/2": "B",
    "K/3": "C",
    # K/I/* status (combined spouses) — defaults to category C; operator
    # can override per employee if the spouse files independently.
    "K/I/0": "C",
    "K/I/1": "C",
    "K/I/2": "C",
    "K/I/3": "C",
}


class HrPayrollTERBracket(models.Model):
    """One row of the TER table — bracket boundary + rate.

    Multiple rows compose the full table per category. The bracket
    semantics: ``rate`` applies when ``lower_bound <= monthly_gross <= upper_bound``.
    ``upper_bound = 0`` means "open-ended" (highest bracket).
    """

    _name = "hr.payroll.ter.bracket"
    _description = "TER Bracket (PP 58/2023)"
    _order = "category, lower_bound"

    category = fields.Selection(
        TER_CATEGORY_SELECTION,
        required=True,
        index=True,
    )
    lower_bound = fields.Float(required=True, help="Inclusive lower bound in IDR.")
    upper_bound = fields.Float(
        help="Inclusive upper bound in IDR. Leave 0 for the highest (open-ended) bracket.",
    )
    rate = fields.Float(
        required=True,
        help="Tarif efektif as a percent (e.g. 5.0 = 5%).",
        digits=(8, 4),
    )
    active = fields.Boolean(default=True)

    @api.constrains("lower_bound", "upper_bound", "rate")
    def _check_bounds(self):
        for rec in self:
            if rec.lower_bound < 0:
                raise ValidationError(_("lower_bound must be ≥ 0"))
            if rec.upper_bound and rec.upper_bound < rec.lower_bound:
                raise ValidationError(_("upper_bound must be ≥ lower_bound (0 = open-ended)"))
            if rec.rate < 0 or rec.rate > 100:
                raise ValidationError(_("rate must be between 0 and 100 percent"))

    @api.model
    def get_rate(self, category: str, monthly_gross: float) -> float:
        """Return the effective rate (as a fraction, e.g. 0.05) for the given gross."""
        if monthly_gross <= 0:
            return 0.0
        brackets = self.sudo().search([("category", "=", category), ("active", "=", True)], order="lower_bound asc")
        for b in brackets:
            if monthly_gross < b.lower_bound:
                continue
            if b.upper_bound == 0 or monthly_gross <= b.upper_bound:
                return b.rate / 100.0
        return 0.0

    @api.model
    def category_for_ptkp(self, ptkp_status: str) -> str:
        return PTKP_TO_TER_CATEGORY.get(ptkp_status, "A")
