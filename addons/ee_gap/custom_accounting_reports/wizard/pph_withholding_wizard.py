# -*- coding: utf-8 -*-
from datetime import date

from odoo import _, fields, models
from odoo.exceptions import UserError


class PphWithholdingWizard(models.TransientModel):
    _name = "custom.report.pph.withholding.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "Rekap PPh Pemotongan Wizard"
    _report_code = "pph_withholding"

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
    pph_kind = fields.Selection(
        [
            ("all", "Semua Jenis"),
            ("pph_22", "PPh 22"),
            ("pph_23", "PPh 23"),
            ("pph_4_2", "PPh 4 ayat (2)"),
            ("pph_26", "PPh 26"),
            ("pph_21", "PPh 21"),
        ],
        string="Jenis PPh",
        default="all",
        required=True,
    )
    posted_only = fields.Boolean(default=True)

    def _build_filters(self):
        self.ensure_one()
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "pph_kind": self.pph_kind,
            "posted_only": self.posted_only,
        }

    def action_print(self):
        self.ensure_one()
        data = {
            "report_code": "pph_withholding",
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
        filename = "Rekap_PPh_%s_%s_%s.xlsx" % (self.pph_kind, self.date_from, self.date_to)
        return self.env["custom.report.pph.withholding"]._xlsx_action(options, filename)

    def action_view_source(self):
        self.ensure_one()
        if "account.move.withholding.line" not in self.env:
            raise UserError(_("Modul PPh (custom_tax_id) belum terpasang."))
        domain = [
            ("company_id", "in", self.company_ids.ids or self.env.companies.ids),
            ("move_id.date", ">=", self.date_from),
            ("move_id.date", "<=", self.date_to),
        ]
        if self.posted_only:
            domain.append(("move_id.state", "=", "posted"))
        if self.pph_kind != "all":
            domain.append(("pph_kind", "=", self.pph_kind))
        return {
            "type": "ir.actions.act_window",
            "name": _("Baris Pemotongan PPh"),
            "res_model": "account.move.withholding.line",
            "view_mode": "list,form",
            "domain": domain,
            "target": "current",
        }
