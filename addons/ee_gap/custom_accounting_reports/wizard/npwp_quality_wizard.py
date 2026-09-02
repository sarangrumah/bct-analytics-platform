# -*- coding: utf-8 -*-
from datetime import date

from odoo import _, fields, models
from odoo.exceptions import UserError


class NpwpQualityWizard(models.TransientModel):
    _name = "custom.report.npwp.quality.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "Data Quality NPWP/NIK Wizard"
    _report_code = "npwp_quality"

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
            "report_code": "npwp_quality",
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
        filename = "Data_Quality_NPWP_%s_%s.xlsx" % (self.date_from, self.date_to)
        return self.env["custom.report.npwp.quality"]._xlsx_action(options, filename)

    def action_view_source(self):
        self.ensure_one()
        Partner = self.env["res.partner"]
        if "x_custom_npwp_status" not in Partner._fields:
            raise UserError(_("Modul PPh (custom_tax_id) belum terpasang."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Lawan Transaksi Bermasalah (NPWP/NIK)"),
            "res_model": "res.partner",
            "view_mode": "list,form",
            "domain": [("x_custom_npwp_status", "in", ("invalid", "none"))],
            "target": "current",
        }
