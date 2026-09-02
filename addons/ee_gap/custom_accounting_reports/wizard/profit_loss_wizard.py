# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields, models


class ProfitLossWizard(models.TransientModel):
    _name = "custom.report.profit.loss.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "Profit & Loss Wizard"
    _report_code = "profit_loss"

    date_from = fields.Date(
        required=True,
        default=lambda self: date.today().replace(month=1, day=1),
    )
    date_to = fields.Date(
        required=True,
        default=lambda self: date.today(),
    )
    company_ids = fields.Many2many(
        "res.company",
        default=lambda self: self.env.companies,
    )
    posted_only = fields.Boolean(default=True)
    comparison = fields.Boolean(string="Show Prior Period")

    def _build_filters(self):
        self.ensure_one()
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "posted_only": self.posted_only,
            "comparison": self.comparison,
        }

    def action_print(self):
        self.ensure_one()
        data = {
            "report_code": "profit_loss",
            "doc_model": self._name,
            "options": {
                **self._build_filters(),
                "date_from": self.date_from.isoformat(),
                "date_to": self.date_to.isoformat(),
            },
        }
        return self.env.ref("custom_accounting_reports.action_report_custom_financial").report_action(self, data=data)

    def action_export_xlsx(self):
        self.ensure_one()
        options = {
            **self._build_filters(),
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
        }
        filename = "Profit_Loss_%s_%s.xlsx" % (self.date_from, self.date_to)
        return self.env["custom.report.profit.loss"]._xlsx_action(options, filename)

    # ------------------------------------------------------------------
    # By-branch variant — same filters, one amount column per Operating Unit.
    # It rides on this wizard rather than owning one so that no new table is
    # added to a module that ships to every tenant.
    # ------------------------------------------------------------------
    def action_view_by_branch(self):
        self.ensure_one()
        title = self.env["custom.report.profit.loss.branch"]._report_title
        return {
            "type": "ir.actions.client",
            "tag": "custom_report_table",
            "name": title,
            "params": {
                "report_code": "profit_loss_branch",
                "options": self._report_options(),
                "context_extra": {},
                "title": title,
            },
        }

    def action_export_xlsx_by_branch(self):
        self.ensure_one()
        options = {
            **self._build_filters(),
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
        }
        filename = "Profit_Loss_by_Branch_%s_%s.xlsx" % (self.date_from, self.date_to)
        return self.env["custom.report.profit.loss.branch"]._xlsx_action(options, filename)
