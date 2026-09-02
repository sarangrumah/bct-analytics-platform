# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields, models


class TrialBalanceWizard(models.TransientModel):
    _name = "custom.report.trial.balance.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "Trial Balance Wizard"
    _report_code = "trial_balance"

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
    journal_ids = fields.Many2many("account.journal")
    posted_only = fields.Boolean(default=True)
    level = fields.Integer(
        default=3,
        help="Account-code depth filter (top N digits). 0 = no filter.",
    )

    def _build_filters(self):
        self.ensure_one()
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "journal_ids": self.journal_ids.ids,
            "posted_only": self.posted_only,
            "level": self.level,
        }

    def action_print(self):
        self.ensure_one()
        data = {
            "report_code": "trial_balance",
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
        filename = "Trial_Balance_%s_%s.xlsx" % (self.date_from, self.date_to)
        return self.env["custom.report.trial.balance"]._xlsx_action(options, filename)
