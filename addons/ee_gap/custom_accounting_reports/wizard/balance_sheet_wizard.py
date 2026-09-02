# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields, models


class BalanceSheetWizard(models.TransientModel):
    _name = "custom.report.balance.sheet.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "Balance Sheet Wizard"
    _report_code = "balance_sheet"

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
    comparison_date_to = fields.Date(string="Prior Period As Of")

    def _build_filters(self):
        self.ensure_one()
        return {
            "date_to": self.date_to,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "posted_only": self.posted_only,
            "comparison": self.comparison,
            "comparison_date_to": self.comparison_date_to,
        }

    def action_print(self):
        self.ensure_one()
        data = {
            "report_code": "balance_sheet",
            "doc_model": self._name,
            "options": {
                **self._build_filters(),
                "date_to": self.date_to.isoformat(),
                "comparison_date_to": (self.comparison_date_to.isoformat() if self.comparison_date_to else None),
            },
        }
        return self.env.ref("custom_accounting_reports.action_report_custom_financial").report_action(self, data=data)

    def action_export_xlsx(self):
        self.ensure_one()
        options = {
            **self._build_filters(),
            "date_to": self.date_to.isoformat(),
            "comparison_date_to": (self.comparison_date_to.isoformat() if self.comparison_date_to else None),
        }
        filename = "Balance_Sheet_%s.xlsx" % self.date_to
        return self.env["custom.report.balance.sheet"]._xlsx_action(options, filename)
