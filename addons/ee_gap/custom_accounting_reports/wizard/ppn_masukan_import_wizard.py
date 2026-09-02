# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields, models


class PpnMasukanImportWizard(models.TransientModel):
    _name = "custom.report.ppn.masukan.import.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "Import PPN Masukan Wizard"
    _report_code = "ppn_masukan_import"

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
    company_ids = fields.Many2many(
        "res.company",
        default=lambda self: self.env.companies,
    )
    partner_ids = fields.Many2many("res.partner", string="Lawan Transaksi")
    posted_only = fields.Boolean(default=True)

    def _build_filters(self):
        self.ensure_one()
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "partner_ids": self.partner_ids.ids,
            "posted_only": self.posted_only,
        }

    def action_print(self):
        self.ensure_one()
        data = {
            "report_code": "ppn_masukan_import",
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
        filename = "Import_PPN_Masukan_%s_%s.xlsx" % (self.date_from, self.date_to)
        return self.env["custom.report.ppn.masukan.import"]._xlsx_action(options, filename)

    def action_view_source(self):
        self.ensure_one()
        domain = [
            ("company_id", "in", self.company_ids.ids or self.env.companies.ids),
            ("move_type", "in", ("in_invoice", "in_refund")),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
        ]
        if self.posted_only:
            domain.append(("state", "=", "posted"))
        if self.partner_ids:
            domain.append(("partner_id", "in", self.partner_ids.ids))
        return {
            "type": "ir.actions.act_window",
            "name": "PPN Masukan",
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": domain,
            "target": "current",
        }
