# -*- coding: utf-8 -*-
from datetime import date

from odoo import _, fields, models
from odoo.exceptions import UserError


class BupotWizard(models.TransientModel):
    _name = "custom.report.bupot.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "Rekap Bukti Potong PPh Wizard"
    _report_code = "bupot"

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
    direction = fields.Selection(
        [
            ("issued", "Diterbitkan (kita sebagai pemotong) — SPT Masa"),
            ("received", "Diterima (dipotong pihak lain) — Kredit Pajak"),
        ],
        string="Arah",
        default="issued",
        required=True,
    )
    pph_kind = fields.Selection(
        [
            ("all", "Semua Jenis"),
            ("22", "PPh 22"),
            ("23", "PPh 23"),
            ("4_2", "PPh 4 ayat (2)"),
            ("15", "PPh 15"),
            ("26", "PPh 26"),
            ("21", "PPh 21"),
        ],
        string="Jenis PPh",
        default="all",
        required=True,
    )

    def _build_filters(self):
        self.ensure_one()
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "direction": self.direction,
            "pph_kind": self.pph_kind,
        }

    def action_print(self):
        self.ensure_one()
        data = {
            "report_code": "bupot",
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
        filename = "Rekap_Bupot_%s_%s_%s.xlsx" % (self.direction, self.date_from, self.date_to)
        return self.env["custom.report.bupot"]._xlsx_action(options, filename)

    def action_view_source(self):
        self.ensure_one()
        if "custom.coretax.bukti.potong" not in self.env:
            raise UserError(_("Modul Bukti Potong (custom_coretax) belum terpasang."))
        domain = [
            ("source", "=", self.direction),
            ("tanggal_bupot", ">=", self.date_from),
            ("tanggal_bupot", "<=", self.date_to),
        ]
        if self.pph_kind != "all":
            domain.append(("jenis_pph", "=", self.pph_kind))
        return {
            "type": "ir.actions.act_window",
            "name": _("Bukti Potong PPh"),
            "res_model": "custom.coretax.bukti.potong",
            "view_mode": "list,form",
            "domain": domain,
            "target": "current",
        }
