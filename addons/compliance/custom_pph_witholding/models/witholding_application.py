# -*- coding: utf-8 -*-
"""Log of every withholding computation/application event."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CustomWitholdingApplication(models.Model):
    _name = "custom.witholding.application"
    _inherit = ["pdp.audited.mixin", "mail.thread", "mail.activity.mixin"]
    _description = "PPh Witholding Application Log"
    _order = "create_date desc, id desc"

    name = fields.Char(
        compute="_compute_name",
        store=True,
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        index=True,
    )
    source_doc = fields.Reference(
        selection="_selection_source_doc",
        string="Source Document",
    )
    pph_type = fields.Selection(
        selection=[
            ("23", "PPh Pasal 23"),
            ("22", "PPh Pasal 22"),
            ("4_2", "PPh Pasal 4 ayat (2)"),
            ("15", "PPh Pasal 15"),
            ("21", "PPh Pasal 21"),
            ("26", "PPh Pasal 26"),
        ],
        required=True,
    )
    service_category = fields.Char()
    gross = fields.Float(digits=(16, 2), required=True)
    rate = fields.Float(digits=(6, 4))
    withheld = fields.Float(digits=(16, 2))
    rule_id = fields.Many2one("custom.witholding.rate", ondelete="restrict")
    has_npwp = fields.Boolean()
    bupot_line_id = fields.Many2one(
        comodel_name="custom.bupot.unifikasi.line",
        ondelete="set null",
        index=True,
    )
    state = fields.Selection(
        selection=[
            ("computed", "Computed"),
            ("applied", "Applied"),
            ("reversed", "Reversed"),
        ],
        default="computed",
        required=True,
        tracking=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )

    @api.model
    def _selection_source_doc(self):
        return [
            ("account.move", "Account Move"),
            ("account.payment", "Account Payment"),
            ("hr.payslip", "Payslip"),
        ]

    @api.depends("partner_id", "pph_type", "withheld")
    def _compute_name(self):
        for rec in self:
            rec.name = f"WH-{rec.pph_type or '?'}/{(rec.partner_id.name or '-')[:20]}/{rec.withheld:,.0f}"

    def action_mark_applied(self):
        for rec in self:
            if rec.state != "computed":
                raise UserError(_("Only Computed applications can be marked Applied."))
            rec.state = "applied"

    def action_reverse(self):
        for rec in self:
            rec.state = "reversed"
