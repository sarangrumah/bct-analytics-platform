# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    x_billable_timesheet_pending_count = fields.Integer(
        string="Billable Timesheets Pending",
        compute="_compute_billable_timesheet_pending_count",
    )

    @api.depends("order_line", "order_line.timesheet_ids")
    def _compute_billable_timesheet_pending_count(self):
        AAL = self.env["account.analytic.line"].sudo()
        for so in self:
            count = AAL.search_count(
                [
                    ("so_line", "in", so.order_line.ids),
                    ("x_billable", "=", True),
                    ("x_validation_state", "=", "validated"),
                    ("x_billed_invoice_line_id", "=", False),
                ]
            )
            so.x_billable_timesheet_pending_count = count

    def action_open_invoice_timesheet_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Invoice Timesheets"),
            "res_model": "custom.timesheet.invoice.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_sale_order_id": self.id,
                "default_partner_id": self.partner_id.id,
            },
        }
