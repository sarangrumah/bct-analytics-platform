# -*- coding: utf-8 -*-
from odoo import api, fields, models


class HrPayrollConfig(models.Model):
    """Singleton-style configuration for Indonesian payroll calculation.

    Operator may edit values when regulation changes; sane PMK-based defaults
    are seeded via data XML.
    """

    _name = "hr.payroll.config"
    _description = "HR Payroll Configuration (Indonesia)"
    _rec_name = "name"

    name = fields.Char(default="Default", required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        ondelete="cascade",
    )

    # ---- Calculation method ----
    calc_method = fields.Selection(
        [
            ("ter", "TER per PP 58/2023 (default since Jan 2024)"),
            ("annualised", "Legacy annualised (pre-2024)"),
        ],
        default="ter",
        required=True,
        help="TER applies a flat monthly bracket per Kategori A/B/C (mapped from PTKP). "
        "December always reconciles via annual progressive regardless of method.",
    )

    # PTKP per PMK 101/2016 (still current as of 2024)
    ptkp_tk0 = fields.Float(string="PTKP TK/0", default=54_000_000)
    ptkp_tk1 = fields.Float(string="PTKP TK/1", default=58_500_000)
    ptkp_tk2 = fields.Float(string="PTKP TK/2", default=63_000_000)
    ptkp_tk3 = fields.Float(string="PTKP TK/3", default=67_500_000)
    ptkp_k0 = fields.Float(string="PTKP K/0", default=58_500_000)
    ptkp_k1 = fields.Float(string="PTKP K/1", default=63_000_000)
    ptkp_k2 = fields.Float(string="PTKP K/2", default=67_500_000)
    ptkp_k3 = fields.Float(string="PTKP K/3", default=72_000_000)
    ptkp_ki0 = fields.Float(string="PTKP K/I/0", default=112_500_000)
    ptkp_ki1 = fields.Float(string="PTKP K/I/1", default=117_000_000)
    ptkp_ki2 = fields.Float(string="PTKP K/I/2", default=121_500_000)
    ptkp_ki3 = fields.Float(string="PTKP K/I/3", default=126_000_000)

    # Biaya jabatan
    biaya_jabatan_pct = fields.Float(string="Biaya Jabatan %", default=5.0)
    biaya_jabatan_max_year = fields.Float(string="Biaya Jabatan Max/Year", default=6_000_000)

    # BPJS Kesehatan (Perpres 64/2020)
    bpjs_kesehatan_ceiling = fields.Float(default=12_000_000)
    bpjs_kesehatan_emp_pct = fields.Float(default=1.0)
    bpjs_kesehatan_company_pct = fields.Float(default=4.0)

    # BPJS Ketenagakerjaan
    bpjs_jht_emp_pct = fields.Float(string="JHT Employee %", default=2.0)
    bpjs_jht_company_pct = fields.Float(string="JHT Company %", default=3.7)
    bpjs_jkk_company_pct = fields.Float(string="JKK Company %", default=0.54)
    bpjs_jkm_company_pct = fields.Float(string="JKM Company %", default=0.30)
    bpjs_jp_emp_pct = fields.Float(string="JP Employee %", default=1.0)
    bpjs_jp_company_pct = fields.Float(string="JP Company %", default=2.0)
    bpjs_jp_ceiling = fields.Float(string="JP Ceiling", default=10_042_300)

    @api.model
    def get_default(self):
        rec = self.search([("active", "=", True), ("company_id", "in", (False, self.env.company.id))], limit=1)
        if not rec:
            rec = self.create({"name": "Default"})
        return rec

    def get_ptkp(self, status):
        self.ensure_one()
        mapping = {
            "TK/0": self.ptkp_tk0,
            "TK/1": self.ptkp_tk1,
            "TK/2": self.ptkp_tk2,
            "TK/3": self.ptkp_tk3,
            "K/0": self.ptkp_k0,
            "K/1": self.ptkp_k1,
            "K/2": self.ptkp_k2,
            "K/3": self.ptkp_k3,
            "K/I/0": self.ptkp_ki0,
            "K/I/1": self.ptkp_ki1,
            "K/I/2": self.ptkp_ki2,
            "K/I/3": self.ptkp_ki3,
        }
        return mapping.get(status, self.ptkp_tk0)
