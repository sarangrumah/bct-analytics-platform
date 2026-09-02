# Part of custom_ppob. Licence: LGPL-3.
"""PPOB biller master data.

A biller is the counterparty a payment is forwarded to: PLN for electricity, PDAM for water, a
telco for airtime, a BPJS office for contributions. It is the grain of the
``dim_biller`` dimension and the denominator of every SLA metric.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

BILLER_CATEGORIES = [
    ("electricity", "Listrik"),
    ("water", "Air"),
    ("telco", "Telekomunikasi"),
    ("internet", "Internet / TV"),
    ("insurance", "Asuransi / BPJS"),
    ("multifinance", "Multifinance"),
    ("tax", "Pajak / Retribusi"),
    ("other", "Lainnya"),
]


class PpobBiller(models.Model):
    _name = "ppob.biller"
    _description = "PPOB Biller"
    _order = "name, id"

    name = fields.Char(required=True, index=True)
    code = fields.Char(
        required=True,
        index=True,
        help="Stable short code. The warehouse uses it as the natural key of dim_biller, so it "
        "must not be recycled between billers.",
    )
    category = fields.Selection(BILLER_CATEGORIES, required=True, default="other", index=True)
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True
    )
    sla_target_seconds = fields.Integer(
        string="SLA Target (s)",
        default=30,
        help="Settlement time the biller is contracted to. ppob.transaction.sla_seconds is "
        "measured against it.",
    )
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint(
        "UNIQUE (code)",
        "A biller code must be unique.",
    )
    _sla_target_positive = models.Constraint(
        "CHECK (sla_target_seconds > 0)",
        "The SLA target must be a positive number of seconds.",
    )

    @api.depends("name", "code")
    def _compute_display_name(self):
        for biller in self:
            biller.display_name = "[%s] %s" % (biller.code or "", biller.name or "")

    @api.constrains("code")
    def _check_code(self):
        for biller in self:
            if not (biller.code or "").strip():
                raise ValidationError(_("A biller code is required."))
