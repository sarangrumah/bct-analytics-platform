# -*- coding: utf-8 -*-
from __future__ import annotations

from odoo import _, fields, models
from odoo.exceptions import UserError


class CustomBastSignWizard(models.TransientModel):
    _name = "custom.bast.sign.wizard"
    _description = "BAST Sign Wizard"

    bast_id = fields.Many2one("custom.bast.document", required=True)
    party = fields.Selection(
        [("from", "From (Releasing)"), ("to", "To (Receiving)")],
        required=True,
        default="to",
    )
    signature = fields.Binary(string="Signature", required=True, attachment=False)
    signed_by = fields.Char(string="Signed By")
    gps_latitude = fields.Float(digits=(10, 7))
    gps_longitude = fields.Float(digits=(10, 7))

    def action_apply(self):
        self.ensure_one()
        if not self.bast_id:
            raise UserError(_("No BAST selected."))
        gps = None
        if self.gps_latitude or self.gps_longitude:
            gps = (self.gps_latitude, self.gps_longitude)
        if self.party == "from":
            self.bast_id.action_sign_from(self.signature, self.signed_by, gps)
        else:
            self.bast_id.action_sign_to(self.signature, self.signed_by, gps)
        return {"type": "ir.actions.act_window_close"}
