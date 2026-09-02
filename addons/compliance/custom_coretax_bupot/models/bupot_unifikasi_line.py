# -*- coding: utf-8 -*-
"""Bukti Potong PPh Unifikasi — line."""

from __future__ import annotations

import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


PPH_TYPES = [
    ("23", "PPh Pasal 23"),
    ("22", "PPh Pasal 22"),
    ("4_2", "PPh Pasal 4 ayat (2)"),
    ("15", "PPh Pasal 15"),
    ("26", "PPh Pasal 26"),
]


_NPWP_RE = re.compile(r"^\d{15,16}$")


class CustomBupotUnifikasiLine(models.Model):
    _name = "custom.bupot.unifikasi.line"
    _inherit = ["pdp.audited.mixin"]
    _description = "Bukti Potong PPh Unifikasi Line"
    _order = "bupot_id, id"

    bupot_id = fields.Many2one(
        comodel_name="custom.bupot.unifikasi",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(related="bupot_id.company_id", store=True)
    internal_ref = fields.Char(
        required=True,
        copy=False,
        default=lambda self: self.env["ir.sequence"].next_by_code("custom.bupot.unifikasi.line") or "/",
        help="Internal reference. Mapped to DJP-assigned bupot_number via the CSV upload wizard.",
    )
    bupot_number = fields.Char(
        string="No. Bukti Potong (DJP)",
        help="Assigned by DJP Coretax after acceptance. Filled via the CSV upload wizard.",
    )
    pph_type = fields.Selection(
        selection=PPH_TYPES,
        required=True,
    )
    cuttee_npwp = fields.Char(string="NPWP Pihak Dipotong")
    cuttee_nitku = fields.Char(string="NITKU Pihak Dipotong")
    cuttee_name = fields.Char(string="Nama Pihak Dipotong", required=True)
    doc_ref = fields.Reference(
        selection=[
            ("account.move", "Account Move"),
            ("account.payment", "Account Payment"),
        ],
        string="Source Document",
    )
    transaction_date = fields.Date(default=fields.Date.context_today)
    gross_amount = fields.Float(
        string="Bruto",
        digits=(16, 2),
        required=True,
    )
    dpp_amount = fields.Float(
        string="DPP",
        digits=(16, 2),
        required=True,
    )
    rate = fields.Float(
        string="Tarif (%)",
        digits=(6, 4),
        required=True,
    )
    withheld_amount = fields.Float(
        string="PPh Dipotong",
        digits=(16, 2),
        required=True,
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )

    @api.constrains("cuttee_npwp")
    def _check_npwp_format(self):
        for rec in self:
            if rec.cuttee_npwp and not _NPWP_RE.match(rec.cuttee_npwp):
                raise ValidationError(
                    _("NPWP %(value)s on line %(ref)s is invalid. Expected 15 or 16 numeric digits.")
                    % {"value": rec.cuttee_npwp, "ref": rec.internal_ref}
                )

    @api.constrains("gross_amount", "dpp_amount", "withheld_amount", "rate")
    def _check_amounts(self):
        for rec in self:
            if rec.gross_amount < 0 or rec.dpp_amount < 0 or rec.withheld_amount < 0:
                raise ValidationError(_("Amounts must be non-negative."))
            if rec.rate < 0 or rec.rate > 100:
                raise ValidationError(_("Rate must be between 0 and 100."))
