# -*- coding: utf-8 -*-
# License: LGPL-3
from __future__ import annotations

from odoo import _, fields, models
from odoo.exceptions import AccessError


class HhtRegenerateSecretWizard(models.TransientModel):
    _name = "hht.regenerate.secret.wizard"
    _description = "Rotate HHT Device API Secret"

    device_id = fields.Many2one(
        "hht.device",
        string="Device",
        required=True,
        default=lambda self: self.env.context.get("active_id"),
    )
    confirm = fields.Boolean(
        string="I understand this will invalidate the existing device secret",
    )
    new_secret_preview = fields.Char(readonly=True)

    def action_rotate(self):
        self.ensure_one()
        if not self.confirm:
            from odoo.exceptions import UserError

            raise UserError(_("Please tick the confirmation checkbox."))
        if not self.env.user.has_group("custom_hht_bridge.group_hht_admin"):
            raise AccessError(_("Only HHT admins may rotate device secrets."))
        self.device_id.action_regenerate_secret()
        self.new_secret_preview = self.device_id.sudo().api_secret
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "context": dict(self.env.context, show_secret=True),
        }
