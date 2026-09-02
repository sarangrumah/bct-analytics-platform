# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields, models


class EventMovementWizard(models.TransientModel):
    _name = "custom.report.event.movement.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "Event Movement Report Wizard"
    _report_code = "event_movement"

    date_from = fields.Date(required=True, default=lambda self: date.today().replace(month=1, day=1))
    date_to = fields.Date(required=True, default=lambda self: date.today())
    company_ids = fields.Many2many("res.company", default=lambda self: self.env.companies)
    partner_ids = fields.Many2many("res.partner", string="Partners")

    def _build_filters(self):
        self.ensure_one()
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "partner_ids": self.partner_ids.ids,
        }

    def action_export_xlsx(self):
        self.ensure_one()
        options = {
            **self._build_filters(),
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
        }
        filename = "Event_Movement_%s_%s.xlsx" % (self.date_from, self.date_to)
        return self.env["custom.report.event.movement"]._xlsx_action(options, filename)
