# -*- coding: utf-8 -*-
from odoo import fields, models


class HrPayslipLine(models.Model):
    _name = "hr.payslip.line"
    _description = "Payslip Line"
    _order = "payslip_id, sequence, id"

    payslip_id = fields.Many2one("hr.payslip", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    code = fields.Char(required=True)
    label = fields.Char(required=True)
    type = fields.Selection(
        [("income", "Income"), ("deduction", "Deduction"), ("info", "Info")],
        required=True,
        default="income",
    )
    amount = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(related="payslip_id.currency_id", store=True)
