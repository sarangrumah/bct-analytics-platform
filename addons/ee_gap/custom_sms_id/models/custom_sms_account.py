# -*- coding: utf-8 -*-
"""SMS account configuration (per-company, per-provider)."""

from __future__ import annotations

from odoo import _, fields, models


class CustomSmsAccount(models.Model):
    _name = "custom.sms.account"
    _description = "SMS Account"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    provider = fields.Selection(
        [
            ("zenziva", "Zenziva (Indonesia)"),
            ("twilio", "Twilio (Global)"),
        ],
        default="zenziva",
        required=True,
        tracking=True,
    )
    api_url = fields.Char(
        string="API URL",
        help="Provider base URL. Defaults vary by provider (e.g. https://console.zenziva.net for Zenziva).",
    )
    # Zenziva-style credentials
    userkey = fields.Char(
        string="User Key",
        help="Zenziva userkey (account identifier).",
    )
    passkey = fields.Char(
        string="Pass Key",
        groups="custom_sms_id.group_manager",
        help="Zenziva passkey. Store encrypted later — move to custom.ir.config encrypted storage before production.",
    )
    # Twilio-style credentials
    account_sid = fields.Char(
        string="Twilio Account SID",
        help="Twilio Account SID (only used when provider = twilio).",
    )
    auth_token = fields.Char(
        string="Twilio Auth Token",
        groups="custom_sms_id.group_manager",
        help="Twilio Auth Token (only used when provider = twilio). Store encrypted later.",
    )
    sender_id = fields.Char(
        string="Sender ID",
        default="CUSTOM",
        help="Alphanumeric sender ID or short-code shown to recipients. Subject to local regulation.",
    )
    is_active = fields.Boolean(default=True, tracking=True)
    sandbox_mode = fields.Boolean(
        default=True,
        help="When enabled, outbound sends are stubbed/logged instead of hitting the live provider API.",
    )
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        required=True,
        index=True,
    )

    # ---------- actions ----------

    def action_test_connection(self):
        """Probe credentials via the resolved adapter — no message sent."""
        self.ensure_one()
        adapter = self.env["custom.sms.adapter.base"]._get_for_account(self)
        result = adapter.test_connection(self)
        msg_type = "success" if result.get("ok") else "warning"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("SMS Connection Test"),
                "message": result.get("message") or "",
                "type": msg_type,
                "sticky": False,
            },
        }
