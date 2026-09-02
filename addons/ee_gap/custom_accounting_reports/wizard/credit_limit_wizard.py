# -*- coding: utf-8 -*-
from odoo import fields, models


class CreditLimitWizard(models.TransientModel):
    _name = "custom.report.credit.limit.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "Credit Limit Report Wizard"
    _report_code = "credit_limit"

    company_ids = fields.Many2many(
        "res.company",
        default=lambda self: self.env.companies,
    )
    partner_ids = fields.Many2many("res.partner", string="Customers")
    only_over_limit = fields.Boolean(string="Only Over-Limit")

    def _build_filters(self):
        self.ensure_one()
        return {
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "partner_ids": self.partner_ids.ids,
            "only_over_limit": self.only_over_limit,
        }

    def action_print(self):
        self.ensure_one()
        data = {
            "report_code": "credit_limit",
            "doc_model": self._name,
            "options": self._build_filters(),
        }
        return self.env.ref("custom_accounting_reports.action_report_custom_financial").report_action(self, data=data)

    def action_export_xlsx(self):
        self.ensure_one()
        options = self._build_filters()
        filename = "Credit_Limit_Report.xlsx"
        return self.env["custom.report.credit.limit"]._xlsx_action(options, filename)
