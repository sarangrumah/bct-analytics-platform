# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CustomTimesheetInvoiceWizard(models.TransientModel):
    _name = "custom.timesheet.invoice.wizard"
    _description = "Invoice Billable Timesheets Wizard"

    sale_order_id = fields.Many2one("sale.order", string="Sale Order")
    partner_id = fields.Many2one("res.partner", string="Customer", required=True)
    date_from = fields.Date(string="Date From", required=True, default=lambda self: fields.Date.context_today(self))
    date_to = fields.Date(string="Date To", required=True, default=lambda self: fields.Date.context_today(self))
    line_ids = fields.One2many(
        "custom.timesheet.invoice.wizard.line",
        "wizard_id",
        string="Timesheet Lines",
    )
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)

    @api.onchange("partner_id", "date_from", "date_to", "sale_order_id")
    def _onchange_filters(self):
        self.line_ids = [(5, 0, 0)]
        if not (self.partner_id and self.date_from and self.date_to):
            return
        domain = self._build_domain()
        AAL = self.env["account.analytic.line"].sudo()
        lines = AAL.search(domain)
        self.line_ids = [
            (
                0,
                0,
                {
                    "analytic_line_id": l.id,
                    "date": l.date,
                    "employee_id": l.employee_id.id,
                    "project_id": l.project_id.id,
                    "unit_amount": l.unit_amount,
                    "billing_rate": l.x_billing_rate,
                    "selected": True,
                },
            )
            for l in lines
        ]

    def _build_domain(self):
        self.ensure_one()
        domain = [
            ("x_billable", "=", True),
            ("x_validation_state", "=", "validated"),
            ("x_billed_invoice_line_id", "=", False),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
        ]
        if self.sale_order_id:
            domain.append(("so_line", "in", self.sale_order_id.order_line.ids))
        else:
            # Filter by partner via project or so_line.partner
            domain.append("|")
            domain.append(("partner_id", "=", self.partner_id.id))
            domain.append(("so_line.order_partner_id", "=", self.partner_id.id))
        return domain

    def action_preview(self):
        self.ensure_one()
        self._onchange_filters()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_create_invoice(self):
        self.ensure_one()
        selected = self.line_ids.filtered("selected")
        if not selected:
            raise UserError(_("Select at least one timesheet line to invoice."))
        AML_vals = []
        analytic_lines = self.env["account.analytic.line"].sudo()
        for w_line in selected:
            aal = w_line.analytic_line_id
            if not aal:
                continue
            if aal.x_billed_invoice_line_id:
                continue
            price = w_line.billing_rate or aal.x_billing_rate or 0.0
            qty = w_line.unit_amount or aal.unit_amount or 0.0
            # Resolve product from sale_line if present.
            product = aal.so_line.product_id if aal.so_line else False
            line_vals = {
                "name": "%s - %s"
                % (
                    aal.project_id.name or "",
                    aal.name or aal.employee_id.name or "",
                ),
                "quantity": qty,
                "price_unit": price,
            }
            if product:
                line_vals["product_id"] = product.id
            if aal.so_line:
                line_vals["sale_line_ids"] = [(4, aal.so_line.id)]
            AML_vals.append((0, 0, line_vals))
            analytic_lines |= aal

        if not AML_vals:
            raise UserError(_("Nothing to invoice."))

        move_vals = {
            "move_type": "out_invoice",
            "partner_id": self.partner_id.id,
            "invoice_date": fields.Date.context_today(self),
            "company_id": self.company_id.id,
            "invoice_line_ids": AML_vals,
        }
        if self.sale_order_id and "invoice_origin" in self.env["account.move"]._fields:
            move_vals["invoice_origin"] = self.sale_order_id.name
        invoice = self.env["account.move"].sudo().create(move_vals)
        # Link each analytic line to its newly-created invoice line.
        inv_lines = invoice.invoice_line_ids
        for aal, inv_line in zip(analytic_lines, inv_lines, strict=False):
            aal.x_billed_invoice_line_id = inv_line.id
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": invoice.id,
            "view_mode": "form",
        }


class CustomTimesheetInvoiceWizardLine(models.TransientModel):
    _name = "custom.timesheet.invoice.wizard.line"
    _description = "Invoice Billable Timesheets Wizard Line"

    wizard_id = fields.Many2one("custom.timesheet.invoice.wizard", required=True, ondelete="cascade")
    analytic_line_id = fields.Many2one("account.analytic.line", required=True, ondelete="cascade")
    selected = fields.Boolean(default=True)
    date = fields.Date()
    employee_id = fields.Many2one("hr.employee")
    project_id = fields.Many2one("project.project")
    unit_amount = fields.Float(string="Hours")
    billing_rate = fields.Float(string="Rate")
