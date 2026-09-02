# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields, models


class PettyCashOutstandingWizard(models.TransientModel):
    _name = "petty.cash.outstanding.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "Petty Cash Outstanding Wizard"
    _report_code = "petty_cash_outstanding"

    date_from = fields.Date(required=True, default=lambda self: date.today().replace(month=1, day=1))
    date_to = fields.Date(string="As Of", required=True, default=lambda self: date.today())
    company_ids = fields.Many2many("res.company", default=lambda self: self.env.companies)
    employee_ids = fields.Many2many("hr.employee", string="Employees")

    def _build_filters(self):
        self.ensure_one()
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "employee_ids": self.employee_ids.ids,
            "posted_only": True,
        }

    def action_print(self):
        self.ensure_one()
        data = {
            "report_code": self._report_code,
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
        filename = "Petty_Cash_Outstanding_%s.xlsx" % self.date_to
        return self.env["petty.cash.report.outstanding"]._xlsx_action(options, filename)
