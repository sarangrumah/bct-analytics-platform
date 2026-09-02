# -*- coding: utf-8 -*-
"""Web-form / external webhook intake tokens.

Each token represents a credential granted to an external system (e.g. a
landing page or chat-bot) to push lead payloads into Odoo. The actual HTTP
endpoint lives in another module; this model holds the token, partner
metadata, and the ingestion logic.
"""

from __future__ import annotations

import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CustomCrmWebFormToken(models.Model):
    _name = "custom.crm.web.form.token"
    _description = "CRM Web-Form / Webhook Intake Token"
    _order = "captured_at desc, id desc"

    name = fields.Char(
        string="Label",
        required=True,
        help="Human-readable label for this token (e.g. 'Landing /demo').",
    )
    token = fields.Char(
        string="Token",
        required=True,
        copy=False,
        index=True,
        default=lambda self: secrets.token_urlsafe(24),
    )
    partner_email = fields.Char(string="Default Partner Email")
    captured_at = fields.Datetime(string="Last Used", readonly=True)
    use_count = fields.Integer(string="Uses", readonly=True, default=0)
    active = fields.Boolean(default=True)
    team_id = fields.Many2one(
        "crm.team",
        string="Sales Team",
        help="Optional team to assign newly created leads to.",
    )

    _token_uniq = models.Constraint(
        "unique(token)",
        "Token must be unique.",
    )

    # ---------- API ----------

    @api.model
    def ingest_payload(self, token: str, data: dict) -> int:
        """Create a crm.lead from a webhook payload identified by ``token``.

        Returns the created lead id. Raises UserError if the token is invalid.
        """
        if not token:
            raise UserError(_("Missing token."))
        rec = self.sudo().search([("token", "=", token), ("active", "=", True)], limit=1)
        if not rec:
            raise UserError(_("Invalid or inactive token."))
        data = dict(data or {})
        lead_vals = {
            "name": data.get("name") or data.get("subject") or _("Web Form Lead"),
            "contact_name": data.get("contact_name") or "",
            "partner_name": data.get("company") or data.get("partner_name") or "",
            "email_from": data.get("email") or rec.partner_email or "",
            "phone": data.get("phone") or "",
            "description": data.get("message") or data.get("description") or "",
            "type": "lead",
            "team_id": rec.team_id.id if rec.team_id else False,
        }
        lead = self.env["crm.lead"].sudo().create(lead_vals)
        rec.sudo().write(
            {
                "captured_at": fields.Datetime.now(),
                "use_count": rec.use_count + 1,
            }
        )
        return lead.id

    def action_rotate_token(self):
        for rec in self:
            rec.token = secrets.token_urlsafe(24)
        return True
