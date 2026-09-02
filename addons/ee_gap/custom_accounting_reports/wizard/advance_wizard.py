# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields, models


class AdvanceWizard(models.TransientModel):
    _name = "custom.report.advance.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "Uang Muka / Down Payment Wizard"
    _report_code = "advance"

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
    account_ids = fields.Many2many(
        "account.account",
        string="Advance Accounts",
        help="Leave empty to auto-detect accounts named like 'Uang Muka' / 'Advance' / 'Prepayment' / 'Down Payment'.",
    )
    posted_only = fields.Boolean(default=True)

    def _build_filters(self):
        self.ensure_one()
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "account_ids": self.account_ids.ids,
            "posted_only": self.posted_only,
        }

    def action_print(self):
        self.ensure_one()
        data = {
            "report_code": "advance",
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
        filename = "Uang_Muka_%s_%s.xlsx" % (self.date_from, self.date_to)
        return self.env["custom.report.advance"]._xlsx_action(options, filename)
