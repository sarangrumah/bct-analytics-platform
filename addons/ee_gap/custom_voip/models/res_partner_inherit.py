# -*- coding: utf-8 -*-
from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    x_custom_voip_call_count = fields.Integer(
        compute="_compute_voip_call_count",
    )

    def _compute_voip_call_count(self):
        Call = self.env["voip.call"].sudo()
        for rec in self:
            rec.x_custom_voip_call_count = Call.search_count(
                [("partner_id", "=", rec.id)],
            )

    def action_voip_call(self):
        self.ensure_one()
        target_number = self.phone
        if not target_number:
            return False
        return self.env["voip.call"].log_outbound(
            partner_id=self.id,
            number=target_number,
        )

    def action_view_voip_calls(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Calls — {self.name}",
            "res_model": "voip.call",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", self.id)],
        }
