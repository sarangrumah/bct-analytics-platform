# -*- coding: utf-8 -*-
import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

PTKP_STATUS = [
    ("TK/0", "TK/0 - Single, no dependants"),
    ("TK/1", "TK/1 - Single, 1 dependant"),
    ("TK/2", "TK/2 - Single, 2 dependants"),
    ("TK/3", "TK/3 - Single, 3 dependants"),
    ("K/0", "K/0 - Married, no dependants"),
    ("K/1", "K/1 - Married, 1 dependant"),
    ("K/2", "K/2 - Married, 2 dependants"),
    ("K/3", "K/3 - Married, 3 dependants"),
    ("K/I/0", "K/I/0 - Married, combined, no dependants"),
    ("K/I/1", "K/I/1 - Married, combined, 1 dependant"),
    ("K/I/2", "K/I/2 - Married, combined, 2 dependants"),
    ("K/I/3", "K/I/3 - Married, combined, 3 dependants"),
]


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    x_custom_npwp = fields.Char(string="NPWP", size=20)
    x_custom_nik = fields.Char(string="NIK", size=20)
    x_custom_kk_no = fields.Char(string="KK Number", size=20)
    x_custom_ptkp_status = fields.Selection(PTKP_STATUS, string="PTKP Status", default="TK/0")
    x_custom_ter_category = fields.Selection(
        [("A", "Kategori A"), ("B", "Kategori B"), ("C", "Kategori C")],
        string="TER Category",
        compute="_compute_ter_category",
        store=True,
        help="Auto-derived from PTKP Status per PP 58/2023. "
        "A = TK/0, TK/1, K/0; B = TK/2, TK/3, K/1, K/2; C = K/3, K/I/*.",
    )
    x_custom_employment_type = fields.Selection(
        [
            ("pegawai_tetap", "Pegawai Tetap"),
            ("pegawai_tidak_tetap", "Pegawai Tidak Tetap / Harian"),
            ("bukan_pegawai", "Bukan Pegawai (Tenaga Ahli)"),
        ],
        default="pegawai_tetap",
        string="Employment Type",
        help="Drives PPh 21 calculation method. TER applies only to Pegawai Tetap.",
    )
    x_custom_bpjs_kesehatan_no = fields.Char(string="BPJS Kesehatan No.")
    x_custom_bpjs_tk_no = fields.Char(string="BPJS Ketenagakerjaan No.")
    x_custom_bank_account = fields.Char(string="Bank Account Number")
    x_custom_bank_name = fields.Char(string="Bank Name")

    @api.depends("x_custom_ptkp_status")
    def _compute_ter_category(self):
        from .hr_payroll_ter import PTKP_TO_TER_CATEGORY

        for rec in self:
            rec.x_custom_ter_category = PTKP_TO_TER_CATEGORY.get(rec.x_custom_ptkp_status or "TK/0", "A")

    @api.constrains("x_custom_nik")
    def _check_nik(self):
        for rec in self:
            if rec.x_custom_nik:
                v = rec.x_custom_nik.strip()
                if not re.fullmatch(r"\d{16}", v):
                    raise ValidationError(_("NIK must be exactly 16 digits."))

    @api.constrains("x_custom_npwp")
    def _check_npwp(self):
        for rec in self:
            if rec.x_custom_npwp:
                # 15-digit legacy or 16-digit NIK-based NPWP, with optional separators
                v = re.sub(r"[^\d]", "", rec.x_custom_npwp)
                if len(v) not in (15, 16):
                    raise ValidationError(_("NPWP must be 15 or 16 digits."))
