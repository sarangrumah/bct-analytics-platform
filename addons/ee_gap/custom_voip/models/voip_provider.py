# -*- coding: utf-8 -*-
from odoo import fields, models


PROVIDER_KINDS = [
    ("manual", "Manual logging only"),
    ("webhook", "Generic webhook"),
    ("asterisk", "Asterisk AMI"),
    ("twilio", "Twilio"),
]


class VoipProvider(models.Model):
    _name = "voip.provider"
    _description = "VoIP Provider Configuration"
    _order = "sequence, name"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    kind = fields.Selection(PROVIDER_KINDS, required=True, default="manual")
    api_base_url = fields.Char()
    account_sid = fields.Char()
    auth_token_set = fields.Boolean(
        compute="_compute_auth_token_set",
        help="True if a token is stored via custom.ir.config encrypted.",
    )
    caller_id = fields.Char(help="Number shown to recipients on outbound calls.")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    def _ir_config_key(self):
        self.ensure_one()
        return f"custom_voip.auth_token.{self.id}"

    def _compute_auth_token_set(self):
        IrCfg = self.env["custom.ir.config"]
        for rec in self:
            try:
                rec.auth_token_set = bool(IrCfg.sudo().get_encrypted(rec._ir_config_key()))
            except Exception:
                rec.auth_token_set = False
