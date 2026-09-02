# -*- coding: utf-8 -*-
"""Partner tax attributes used by withholding resolution."""

from __future__ import annotations

import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Indonesian NPWP is either 15 digits (legacy) or 16 digits (NIK-based since 2024).
NPWP_15_RE = re.compile(r"^\d{15}$")
NPWP_16_RE = re.compile(r"^\d{16}$")
NIK_RE = re.compile(r"^\d{16}$")
# Coretax expects the counterparty NITKU as 22 digits: the 16-digit NPWP
# followed by the 6-digit tempat-kegiatan-usaha suffix.
NITKU_22_RE = re.compile(r"^\d{22}$")
# The pemotong's own NITKU is carried as the 6-digit suffix alone.
NITKU_6_RE = re.compile(r"^\d{6}$")

PTKP_SELECTION = [
    ("TK/0", "TK/0"),
    ("TK/1", "TK/1"),
    ("TK/2", "TK/2"),
    ("TK/3", "TK/3"),
    ("K/0", "K/0"),
    ("K/1", "K/1"),
    ("K/2", "K/2"),
    ("K/3", "K/3"),
    ("K/I/0", "K/I/0"),
    ("K/I/1", "K/I/1"),
    ("K/I/2", "K/I/2"),
    ("K/I/3", "K/I/3"),
]


class ResPartner(models.Model):
    _inherit = "res.partner"

    x_custom_npwp = fields.Char(
        string="NPWP",
        help="Nomor Pokok Wajib Pajak. 15-digit legacy or 16-digit (NIK-based, post-2024).",
    )
    x_custom_nik = fields.Char(
        string="NIK (Custom)",
        help="Nomor Induk Kependudukan (16-digit). For individual partners (orang pribadi).",
    )
    x_custom_npwp_status = fields.Selection(
        [
            ("valid", "Valid NPWP"),
            ("invalid", "Invalid"),
            ("none", "Tidak ada NPWP"),
        ],
        compute="_compute_npwp_status",
        store=True,
    )
    x_custom_has_valid_npwp = fields.Boolean(
        compute="_compute_has_valid_npwp",
        store=True,
    )
    x_custom_pkp = fields.Boolean(
        string="PKP",
        help="Pengusaha Kena Pajak — registered for PPN. Affects fiscal position.",
    )
    x_custom_foreign_counterparty = fields.Boolean(
        string="Wajib Pajak Luar Negeri",
        compute="_compute_foreign_counterparty",
        store=True,
        help="Auto-set when the partner's country differs from the company country.",
    )
    x_custom_nitku = fields.Char(
        string="NITKU",
        help="Nomor Identitas Tempat Kegiatan Usaha (22 digit): NPWP 16 digit + "
        "6 digit suffix. Falls back to NPWP + '000000' when left empty.",
    )
    x_custom_tin = fields.Char(
        string="TIN (WP Luar Negeri)",
        help="Tax Identity Number of a foreign counterparty. Used by the Bupot Non-Resident export in place of NPWP.",
    )
    x_custom_passport = fields.Char(string="Nomor Passport")
    x_custom_kitas = fields.Char(string="Nomor KITAP/KITAS")
    x_custom_birth_place = fields.Char(string="Tempat Lahir")
    x_custom_birth_date = fields.Date(string="Tanggal Lahir")
    x_custom_ptkp = fields.Selection(
        PTKP_SELECTION,
        string="PTKP",
        help="Penghasilan Tidak Kena Pajak status. Required by the Bupot PPh 21 export.",
    )

    @api.depends("x_custom_npwp")
    def _compute_nitku_display(self):
        for rec in self:
            rec.x_custom_nitku_display = rec._custom_coretax_nitku()

    x_custom_nitku_display = fields.Char(
        string="NITKU (efektif)",
        compute="_compute_nitku_display",
        help="The value the Coretax exports will actually emit.",
    )

    def _custom_coretax_nitku(self):
        """NITKU as Coretax wants it: explicit value, else NPWP + '000000'.

        DJP treats a taxpayer with no separate tempat kegiatan usaha as having
        the head-office suffix ``000000``, so deriving it is safe and spares
        operators from typing the NPWP twice.
        """
        self.ensure_one()
        if self.x_custom_nitku:
            return self.x_custom_nitku
        npwp = (self.x_custom_npwp or "").replace(".", "").replace("-", "")
        if NPWP_16_RE.match(npwp):
            return npwp + "000000"
        return ""

    @api.depends("x_custom_npwp")
    def _compute_npwp_status(self):
        for rec in self:
            v = (rec.x_custom_npwp or "").replace(".", "").replace("-", "")
            if not v:
                rec.x_custom_npwp_status = "none"
            elif NPWP_15_RE.match(v) or NPWP_16_RE.match(v):
                rec.x_custom_npwp_status = "valid"
            else:
                rec.x_custom_npwp_status = "invalid"

    @api.depends("x_custom_npwp_status")
    def _compute_has_valid_npwp(self):
        for rec in self:
            rec.x_custom_has_valid_npwp = rec.x_custom_npwp_status == "valid"

    @api.depends("country_id", "company_id", "country_id.code")
    def _compute_foreign_counterparty(self):
        for rec in self:
            company = rec.company_id or rec.env.company
            if not company.country_id or not rec.country_id:
                rec.x_custom_foreign_counterparty = False
            else:
                rec.x_custom_foreign_counterparty = rec.country_id != company.country_id

    @api.constrains("x_custom_nik")
    def _check_nik(self):
        for rec in self:
            if rec.x_custom_nik and not NIK_RE.match(rec.x_custom_nik):
                raise ValidationError(_("NIK harus 16 digit angka."))

    @api.constrains("x_custom_nitku")
    def _check_nitku(self):
        for rec in self:
            if rec.x_custom_nitku and not NITKU_22_RE.match(rec.x_custom_nitku):
                raise ValidationError(_("NITKU harus 22 digit angka (NPWP 16 digit + 6 digit tempat kegiatan usaha)."))

    def check_vat_id(self, vat):
        """Relaxed Indonesian NPWP validation for the standard ``vat`` field.

        Core ``base_vat.check_vat_id`` requires a Luhn checksum on legacy
        15-digit NPWPs (and special-cases a leading ``0``), which rejects
        otherwise-valid numbers such as ``015556667008000``. Per business
        requirement we accept any 15- or 16-digit NPWP (legacy 15-digit or
        NIK-based 16-digit, post-2024), ignoring ``.``/``-``/space
        separators, and drop the checksum / leading-zero rules.
        """
        digits = re.sub(r"[ .\-]", "", vat or "")
        return bool(NPWP_15_RE.match(digits) or NPWP_16_RE.match(digits))
