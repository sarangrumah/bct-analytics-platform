# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields, models


class SalesDetailWizard(models.TransientModel):
    _name = "custom.report.sales.detail.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "Sales Detail (X24DN) Wizard"
    _report_code = "sales_detail"

    date_from = fields.Date(required=True, default=lambda self: date.today().replace(day=1))
    date_to = fields.Date(required=True, default=lambda self: date.today())
    company_ids = fields.Many2many("res.company", default=lambda self: self.env.companies)
    # A Many2many to pos.config would make this shared module unloadable on any
    # tenant without point_of_sale installed (the tenant, PPOB): the comodel is
    # resolved when the registry is built, not when the report runs. A store-code
    # filter also matches how the client identifies stores (80435, 80431 ...).
    store_codes = fields.Char(
        string="Store Code",
        help="Kode store XStore, pisahkan dengan koma. Kosongkan untuk semua store.",
    )
    categ_ids = fields.Many2many("product.category", string="Kategori Produk")

    def _store_code_list(self):
        self.ensure_one()
        return [c.strip() for c in (self.store_codes or "").split(",") if c.strip()]

    def _build_filters(self):
        self.ensure_one()
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "store_codes": self._store_code_list(),
            "categ_ids": self.categ_ids.ids,
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
        filename = "Sales_Detail_X24DN_%s_%s.xlsx" % (self.date_from, self.date_to)
        return self.env["custom.report.sales.detail"]._xlsx_action(self._report_options(), filename)
