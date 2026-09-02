# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields, models


class SparePartsWizard(models.TransientModel):
    _name = "custom.report.spareparts.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "Spare Parts Report Wizard"
    _report_code = "spareparts"

    date_from = fields.Date(required=True, default=lambda self: date.today().replace(month=1, day=1))
    date_to = fields.Date(required=True, default=lambda self: date.today())
    company_ids = fields.Many2many("res.company", default=lambda self: self.env.companies)

    def _build_filters(self):
        self.ensure_one()
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
        }

    def action_export_xlsx(self):
        self.ensure_one()
        options = {
            **self._build_filters(),
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
        }
        filename = "Spare_Parts_%s_%s.xlsx" % (self.date_from, self.date_to)
        return self.env["custom.report.spareparts"]._xlsx_action(options, filename)
