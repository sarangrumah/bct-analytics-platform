# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields, models


class GlOpenItemsWizard(models.TransientModel):
    _name = "custom.report.gl.open.items.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "GL Open Items Wizard"
    _report_code = "gl_open_items"

    # No date_from default: open items are cumulative by nature, so the report
    # should reach back to the first entry unless the user narrows it.
    date_from = fields.Date(string="Dari Tanggal")
    date_to = fields.Date(
        string="Posisi Per Tanggal",
        required=True,
        default=lambda self: date.today(),
        help="Outstanding dihitung sesuai rekonsiliasi yang sudah terjadi sampai tanggal ini.",
    )
    company_ids = fields.Many2many("res.company", default=lambda self: self.env.companies)
    partner_ids = fields.Many2many("res.partner", string="Lawan Transaksi")
    account_ids = fields.Many2many(
        "account.account",
        string="Akun",
        domain=[("reconcile", "=", True)],
        help="Kosongkan untuk semua akun yang bisa direkonsiliasi.",
    )
    account_type_filter = fields.Selection(
        [
            ("all", "Semua akun rekonsiliasi"),
            ("receivable", "Piutang saja"),
            ("payable", "Hutang saja"),
            ("clearing", "Selain piutang/hutang (clearing)"),
        ],
        string="Kelompok Akun",
        default="all",
        required=True,
    )

    _TYPES = {
        "receivable": ["asset_receivable"],
        "payable": ["liability_payable"],
    }

    def _account_types(self):
        self.ensure_one()
        if self.account_type_filter in self._TYPES:
            return self._TYPES[self.account_type_filter]
        if self.account_type_filter == "clearing":
            types = self.env["account.account"]._fields["account_type"].get_values(self.env)
            return [t for t in types if t not in ("asset_receivable", "liability_payable")]
        return []

    def _build_filters(self):
        self.ensure_one()
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "partner_ids": self.partner_ids.ids,
            "account_ids": self.account_ids.ids,
            "account_types": self._account_types(),
        }

    def _report_options(self):
        self.ensure_one()
        options = dict(self._build_filters())
        options["date_to"] = self.date_to.isoformat()
        if self.date_from:
            options["date_from"] = self.date_from.isoformat()
        return options

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
        filename = "GL_Open_Items_%s.xlsx" % self.date_to
        return self.env["custom.report.gl.open.items"]._xlsx_action(self._report_options(), filename)
