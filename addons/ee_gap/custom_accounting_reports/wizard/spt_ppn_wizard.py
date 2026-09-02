# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields, models


class SptPpnWizard(models.TransientModel):
    _name = "custom.report.spt.ppn.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "SPT Masa PPN 1111 (Induk) Wizard"
    _report_code = "spt_ppn"

    date_from = fields.Date(
        string="Masa Dari",
        required=True,
        default=lambda self: date.today().replace(day=1),
    )
    date_to = fields.Date(
        string="Masa Sampai",
        required=True,
        default=lambda self: date.today(),
    )
    company_ids = fields.Many2many("res.company", default=lambda self: self.env.companies)
    posted_only = fields.Boolean(default=True)

    def _build_filters(self):
        self.ensure_one()
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "posted_only": self.posted_only,
        }

    def action_print(self):
        self.ensure_one()
        data = {
            "report_code": "spt_ppn",
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
        filename = "SPT_Masa_PPN_%s_%s.xlsx" % (self.date_from, self.date_to)
        return self.env["custom.report.spt.ppn"]._xlsx_action(options, filename)
