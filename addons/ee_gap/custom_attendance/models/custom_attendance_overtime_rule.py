# -*- coding: utf-8 -*-
from odoo import fields, models


class CustomAttendanceOvertimeRule(models.Model):
    _name = "custom.attendance.overtime.rule"
    _description = "Attendance Overtime Rule"
    _order = "sequence, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    threshold_hours = fields.Float(
        string="Daily Threshold (h)",
        default=8.0,
        help="Worked hours above this threshold are counted as overtime.",
    )
    multiplier = fields.Float(
        string="OT Multiplier",
        default=1.5,
        help="Pay multiplier applied to overtime hours.",
    )
    differential = fields.Selection(
        [
            ("weekday", "Weekday"),
            ("weekend", "Weekend"),
            ("holiday", "Public Holiday"),
        ],
        string="Applies To",
        default="weekday",
        required=True,
    )
    is_active = fields.Boolean(string="Active", default=True)
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
    )
    note = fields.Text(string="Notes")

    _threshold_positive = models.Constraint(
        "CHECK(threshold_hours >= 0)",
        "Threshold hours must be non-negative.",
    )
    _multiplier_positive = models.Constraint(
        "CHECK(multiplier > 0)",
        "Multiplier must be greater than zero.",
    )
