# -*- coding: utf-8 -*-
from odoo import fields, models


PLATFORMS = [
    ("facebook", "Facebook"),
    ("instagram", "Instagram"),
    ("x", "X (Twitter)"),
    ("linkedin", "LinkedIn"),
    ("tiktok", "TikTok"),
    ("youtube", "YouTube"),
]


class SocialAccount(models.Model):
    _name = "social.account"
    _description = "Social Media Account"
    _order = "platform, handle"

    name = fields.Char(required=True)
    platform = fields.Selection(PLATFORMS, required=True, index=True)
    handle = fields.Char(required=True, help="@handle or page id")
    api_token_set = fields.Boolean(compute="_compute_token_set")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    _uniq_platform_handle = models.Constraint(
        "unique(platform, handle)",
        "Account already registered for this platform.",
    )

    def _ir_config_key(self):
        self.ensure_one()
        return f"custom_social.api_token.{self.id}"

    def _compute_token_set(self):
        IrCfg = self.env["custom.ir.config"]
        for rec in self:
            try:
                rec.api_token_set = bool(IrCfg.sudo().get_encrypted(rec._ir_config_key()))
            except Exception:
                rec.api_token_set = False
