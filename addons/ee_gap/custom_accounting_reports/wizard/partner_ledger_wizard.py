# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields, models


class PartnerLedgerWizard(models.TransientModel):
    _name = "custom.report.partner.ledger.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "Partner Ledger Wizard"
    _report_code = "partner_ledger"

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
    partner_ids = fields.Many2many("res.partner")
    partner_kind = fields.Selection(
        selection=[
            ("both", "Receivable + Payable"),
            ("receivable", "Receivable Only"),
            ("payable", "Payable Only"),
        ],
        default="both",
        required=True,
    )
    posted_only = fields.Boolean(default=True)

    def _build_filters(self):
        self.ensure_one()
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "partner_ids": self.partner_ids.ids,
            "partner_kind": self.partner_kind,
            "posted_only": self.posted_only,
        }

    def action_print(self):
        self.ensure_one()
        data = {
            "report_code": "partner_ledger",
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
        filename = "Partner_Ledger_%s_%s.xlsx" % (self.date_from, self.date_to)
        return self.env["custom.report.partner.ledger"]._xlsx_action(options, filename)
