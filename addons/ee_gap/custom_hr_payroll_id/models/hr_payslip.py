# -*- coding: utf-8 -*-
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# PPh 21 progressive bracket per UU HPP (Cipta Kerja regime).
# Tuples: (upper_bound, rate). The last bracket has upper_bound = None (open-ended).
PPH21_BRACKETS = [
    (60_000_000, 0.05),
    (250_000_000, 0.15),
    (500_000_000, 0.25),
    (5_000_000_000, 0.30),
    (None, 0.35),
]


def _compute_pph21(taxable_year: float) -> float:
    """Apply progressive brackets to annual taxable income."""
    if taxable_year <= 0:
        return 0.0
    remaining = taxable_year
    prev = 0.0
    tax = 0.0
    for upper, rate in PPH21_BRACKETS:
        if upper is None:
            tax += remaining * rate
            remaining = 0
            break
        slab = upper - prev
        if remaining <= slab:
            tax += remaining * rate
            remaining = 0
            break
        tax += slab * rate
        remaining -= slab
        prev = upper
    return tax


class HrPayslip(models.Model):
    _name = "hr.payslip"
    _description = "Payslip (Indonesia)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "period_year desc, period_month desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    employee_id = fields.Many2one("hr.employee", required=True, tracking=True)
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        required=True,
    )
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id", store=True)
    period_year = fields.Integer(required=True, default=lambda s: fields.Date.today().year)
    period_month = fields.Selection(
        [(str(i), f"{i:02d}") for i in range(1, 13)],
        required=True,
        default=lambda s: str(fields.Date.today().month),
    )
    is_thr = fields.Boolean(string="THR Run")

    gross_salary = fields.Monetary(currency_field="currency_id", tracking=True)
    tunjangan_jabatan = fields.Monetary(currency_field="currency_id")
    tunjangan_lain = fields.Monetary(currency_field="currency_id")

    bpjs_kesehatan_emp = fields.Monetary(currency_field="currency_id", readonly=True)
    bpjs_kesehatan_company = fields.Monetary(currency_field="currency_id", readonly=True)
    bpjs_jht_emp = fields.Monetary(currency_field="currency_id", readonly=True)
    bpjs_jht_company = fields.Monetary(currency_field="currency_id", readonly=True)
    bpjs_jp_emp = fields.Monetary(currency_field="currency_id", readonly=True)
    bpjs_jp_company = fields.Monetary(currency_field="currency_id", readonly=True)
    bpjs_jkk = fields.Monetary(currency_field="currency_id", readonly=True)
    bpjs_jkm = fields.Monetary(currency_field="currency_id", readonly=True)
    pph21 = fields.Monetary(currency_field="currency_id", readonly=True, tracking=True)
    take_home_pay = fields.Monetary(currency_field="currency_id", readonly=True, tracking=True)

    line_ids = fields.One2many("hr.payslip.line", "payslip_id", string="Lines")

    # Calculation method used at compute time (cached for audit + reporting).
    calc_method_used = fields.Selection(
        [
            ("ter", "TER (PP 58/2023)"),
            ("annualised", "Legacy annualised"),
            ("annual_recon", "Annual Recon (December)"),
        ],
        readonly=True,
    )
    ter_category_used = fields.Selection(
        [("A", "A"), ("B", "B"), ("C", "C")],
        readonly=True,
    )
    ter_rate_used = fields.Float(string="TER Rate (%) used", digits=(6, 4), readonly=True)

    # Coretax bridge — one Bupot PPh 21 per payslip (materialised on approve)
    bupot_id = fields.Many2one(
        "custom.coretax.bukti.potong",
        string="Bupot PPh 21",
        readonly=True,
        copy=False,
    )

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("computed", "Computed"),
            ("approved", "Approved"),
            ("paid", "Paid"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )

    _uniq_employee_period = models.Constraint(
        "unique(employee_id, period_year, period_month, is_thr)",
        "Only one payslip per employee per period (regular vs THR are distinct).",
    )

    @api.depends("employee_id", "period_year", "period_month", "is_thr")
    def _compute_name(self):
        for r in self:
            tag = "THR" if r.is_thr else "Reg"
            r.name = "%s/%s/%s-%s" % (
                tag,
                r.period_year or "----",
                r.period_month or "--",
                r.employee_id.name or "?",
            )

    # ---------- compute action ----------

    def action_compute(self):
        config = self.env["hr.payroll.config"].get_default()
        for slip in self:
            slip._do_compute(config)
            slip._pdp_audit("compute")
        return True

    def _do_compute(self, config):
        self.ensure_one()
        # Clear previous lines
        self.line_ids.unlink()

        gross_month = self.gross_salary or 0.0
        tj = self.tunjangan_jabatan or 0.0
        tl = self.tunjangan_lain or 0.0
        gross_total_month = gross_month + tj + tl

        # ---------- BPJS Kesehatan ----------
        kes_base = min(gross_total_month, config.bpjs_kesehatan_ceiling)
        bpjs_kes_emp = kes_base * (config.bpjs_kesehatan_emp_pct / 100.0)
        bpjs_kes_co = kes_base * (config.bpjs_kesehatan_company_pct / 100.0)

        # ---------- BPJS Ketenagakerjaan ----------
        bpjs_jht_emp = gross_total_month * (config.bpjs_jht_emp_pct / 100.0)
        bpjs_jht_co = gross_total_month * (config.bpjs_jht_company_pct / 100.0)
        jp_base = min(gross_total_month, config.bpjs_jp_ceiling)
        bpjs_jp_emp = jp_base * (config.bpjs_jp_emp_pct / 100.0)
        bpjs_jp_co = jp_base * (config.bpjs_jp_company_pct / 100.0)
        bpjs_jkk = gross_total_month * (config.bpjs_jkk_company_pct / 100.0)
        bpjs_jkm = gross_total_month * (config.bpjs_jkm_company_pct / 100.0)

        # ---------- PPh 21 ----------
        method_used = "annualised"
        ter_cat = False
        ter_rate = 0.0

        if self.is_thr:
            # THR: tax on THR alone using progressive brackets on annual basis.
            # For MVP, treat THR as a one-month gross (gross_month is the THR amount).
            ptkp = config.get_ptkp(self.employee_id.x_custom_ptkp_status or "TK/0")
            taxable_year = max(0.0, gross_total_month - ptkp)
            pph_year = _compute_pph21(taxable_year)
            pph_month = pph_year
            method_used = "annual_recon"
        elif (
            config.calc_method == "ter"
            and self.employee_id.x_custom_employment_type == "pegawai_tetap"
            and int(self.period_month or 0) != 12
        ):
            # TER (PP 58/2023): flat monthly bracket per Kategori A/B/C.
            # Year-end (December) always falls through to annualised reconciliation.
            ter_cat = self.employee_id.x_custom_ter_category or "A"
            ter_table = self.env["hr.payroll.ter.bracket"].sudo()
            rate_fraction = ter_table.get_rate(ter_cat, gross_total_month)
            ter_rate = rate_fraction * 100.0
            pph_month = gross_total_month * rate_fraction
            method_used = "ter"
        else:
            # Annualised fallback (December reconciliation OR config.calc_method='annualised').
            annual_gross = gross_total_month * 12
            biaya_jabatan_year = min(
                annual_gross * (config.biaya_jabatan_pct / 100.0),
                config.biaya_jabatan_max_year,
            )
            jht_emp_year = bpjs_jht_emp * 12
            jp_emp_year = bpjs_jp_emp * 12
            net_year = annual_gross - biaya_jabatan_year - jht_emp_year - jp_emp_year
            ptkp = config.get_ptkp(self.employee_id.x_custom_ptkp_status or "TK/0")
            taxable_year = max(0.0, net_year - ptkp)
            pph_year = _compute_pph21(taxable_year)
            pph_month = pph_year / 12.0
            method_used = "annualised" if int(self.period_month or 0) != 12 else "annual_recon"

        # ---------- Take-home ----------
        deductions = bpjs_kes_emp + bpjs_jht_emp + bpjs_jp_emp + pph_month
        thp = gross_total_month - deductions

        # Persist computed totals
        self.write(
            {
                "bpjs_kesehatan_emp": bpjs_kes_emp,
                "bpjs_kesehatan_company": bpjs_kes_co,
                "bpjs_jht_emp": bpjs_jht_emp,
                "bpjs_jht_company": bpjs_jht_co,
                "bpjs_jp_emp": bpjs_jp_emp,
                "bpjs_jp_company": bpjs_jp_co,
                "bpjs_jkk": bpjs_jkk,
                "bpjs_jkm": bpjs_jkm,
                "pph21": pph_month,
                "take_home_pay": thp,
                "calc_method_used": method_used,
                "ter_category_used": ter_cat,
                "ter_rate_used": ter_rate,
                "state": "computed",
            }
        )

        # Generate breakdown lines
        Line = self.env["hr.payslip.line"]
        seq = 10
        line_vals = [
            (10, "GROSS", "Gaji Pokok", "income", gross_month),
            (20, "TJ", "Tunjangan Jabatan", "income", tj),
            (30, "TL", "Tunjangan Lain", "income", tl),
            (100, "BPJS_KES", "BPJS Kesehatan (employee)", "deduction", bpjs_kes_emp),
            (110, "BPJS_JHT", "BPJS JHT (employee)", "deduction", bpjs_jht_emp),
            (120, "BPJS_JP", "BPJS JP (employee)", "deduction", bpjs_jp_emp),
            (130, "PPH21", "PPh 21", "deduction", pph_month),
            (200, "THP", "Take Home Pay", "info", thp),
        ]
        for s, code, label, typ, amt in line_vals:
            if amt or typ == "info":
                Line.create(
                    {
                        "payslip_id": self.id,
                        "sequence": s,
                        "code": code,
                        "label": label,
                        "type": typ,
                        "amount": amt,
                    }
                )

    # ---------- workflow ----------

    def action_approve(self):
        self.write({"state": "approved"})
        for r in self:
            r._materialise_bupot_pph21()
            r._pdp_audit("approve")

    def _materialise_bupot_pph21(self):
        """Create a draft Bupot PPh 21 for this payslip if PPh > 0.

        Idempotent: skips if ``bupot_id`` already set.
        """
        self.ensure_one()
        if self.bupot_id or not self.pph21 or self.pph21 <= 0:
            return
        Bupot = self.env["custom.coretax.bukti.potong"].sudo()
        partner = self.employee_id.user_partner_id or self.employee_id.work_contact_id
        if not partner:
            # Fall back to employee resource_id name; create a minimal partner so
            # the Bupot has a counterparty reference. Operator can later replace.
            partner = (
                self.env["res.partner"]
                .sudo()
                .create(
                    {
                        "name": self.employee_id.name,
                        "is_company": False,
                    }
                )
            )
        try:
            self.bupot_id = Bupot.create(
                {
                    "no_bupot": f"DRAFT-PPH21-{self.period_year}{self.period_month:0>2}-{self.employee_id.id}",
                    "partner_id": partner.id,
                    "jenis_pph": "21",
                    "tarif": self.ter_rate_used or 0.0,
                    "dpp": self.gross_salary + (self.tunjangan_jabatan or 0) + (self.tunjangan_lain or 0),
                    "pph_terpotong": self.pph21,
                    "currency_id": self.currency_id.id,
                    "tanggal_bupot": fields.Date.context_today(self),
                    "period_year": self.period_year,
                    "period_month": int(self.period_month or 0),
                    "source": "issued",
                    "state": "draft",
                }
            ).id
        except Exception as e:
            _logger.warning("Failed to create Bupot PPh 21 for payslip %s: %s", self.name, e)
            self.message_post(body=_("Failed to auto-create Bupot PPh 21: %s") % e)

    def action_pay(self):
        self.write({"state": "paid"})
        for r in self:
            r._pdp_audit("pay")

    def action_draft(self):
        self.write({"state": "draft"})

    def write(self, vals):
        # State-transition guard: don't allow editing financial fields once approved/paid
        protected = {"gross_salary", "tunjangan_jabatan", "tunjangan_lain"}
        if vals.keys() & protected:
            for r in self:
                if r.state in ("approved", "paid"):
                    raise UserError(_("Cannot modify approved/paid payslip %s.") % r.name)
        return super().write(vals)

    # ---------- audit ----------

    def _pdp_audit(self, action_label):
        try:
            self.ensure_one()
            user = self.env.user
            payload = {
                "action": action_label,
                "payslip": self.name,
                "state": self.state,
                "thp": float(self.take_home_pay or 0),
                "pph21": float(self.pph21 or 0),
            }
            self.env.cr.execute(
                """
                INSERT INTO pdp.audit_log (
                    actor_user_id, actor_login, tenant_db,
                    model_name, res_id, action,
                    field_changes, classification
                ) VALUES (%s, %s, %s, %s, %s, 'write', %s::jsonb, 'financial')
                """,
                (
                    user.id if user else None,
                    user.login if user else None,
                    self.env.cr.dbname,
                    self._name,
                    self.id,
                    json.dumps(payload, default=str),
                ),
            )
        except Exception as e:  # pragma: no cover
            _logger.warning("payslip audit log failed: %s", e)
