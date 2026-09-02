# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields, models


class BillPaymentWizard(models.TransientModel):
    _name = "custom.report.bill.payment.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "Bill vs Payment Mapping Wizard"
    _report_code = "bill_payment"

    date_from = fields.Date(required=True, default=lambda self: date.today().replace(month=1, day=1))
    date_to = fields.Date(required=True, default=lambda self: date.today())
    company_ids = fields.Many2many("res.company", default=lambda self: self.env.companies)
    partner_ids = fields.Many2many("res.partner", string="Vendor")
    posted_only = fields.Boolean(string="Hanya Posted", default=True)

    def _build_filters(self):
        self.ensure_one()
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "partner_ids": self.partner_ids.ids,
            "posted_only": self.posted_only,
        }

    def _report_options(self):
        self.ensure_one()
        return {
            **self._build_filters(),
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
        }

    def action_print(self):
        self.ensure_one()
        data = {
            "report_code": self._report_code,
            "doc_model": self._name,
            "options": self._report_options(),
        }
        return self.env.ref("custom_accounting_reports.action_report_custom_financial").report_action(self, data=data)

    def action_export_xlsx(self):
        self.ensure_one()
        filename = "Bill_vs_Payment_%s_%s.xlsx" % (self.date_from, self.date_to)
        return self.env["custom.report.bill.payment"]._xlsx_action(self._report_options(), filename)
