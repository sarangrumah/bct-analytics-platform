# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields, models


class SalesWizard(models.TransientModel):
    _name = "custom.report.sales.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "Sales Report Wizard"
    _report_code = "sales"

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
    partner_ids = fields.Many2many("res.partner", string="Customers")
    basis = fields.Selection(
        [
            ("document", "Invoice + POS"),
            ("gl", "Akun Pendapatan (GL)"),
        ],
        string="Sumber Data",
        default="document",
        required=True,
        help="Invoice + POS membaca dokumen penjualan. Pilih Akun Pendapatan (GL) "
        "bila penjualan dibukukan lewat jurnal — misalnya saldo awal — sehingga "
        "tidak muncul sebagai invoice maupun transaksi POS.",
    )
    group_by = fields.Selection(
        [
            ("none", "No grouping"),
            ("customer", "By Customer"),
            ("product", "By Product"),
            ("month", "By Month"),
        ],
        string="Group By",
        default="none",
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
            "group_by": self.group_by,
            "basis": self.basis,
            "posted_only": self.posted_only,
        }

    def action_print(self):
        self.ensure_one()
        data = {
            "report_code": "sales",
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
        filename = "Sales_Report_%s_%s.xlsx" % (self.date_from, self.date_to)
        return self.env["custom.report.sales"]._xlsx_action(options, filename)
