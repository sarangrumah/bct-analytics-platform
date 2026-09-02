# -*- coding: utf-8 -*-
"""Outbound SMS message queue with PDP consent gating."""

from __future__ import annotations

import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


# Purpose -> consent purpose code mapping.
# Marketing is hard-gated; OTP / transactional log-warn only.
_PURPOSE_CONSENT_CODE = {
    "marketing": "sms_marketing",
    "transactional": "sms_transactional",
    "otp": "sms_transactional",
}


class CustomSmsMessage(models.Model):
    _name = "custom.sms.message"
    _description = "SMS Message"
    _inherit = ["mail.thread", "pdp.audited.mixin"]
    _order = "create_date desc"

    account_id = fields.Many2one(
        "custom.sms.account",
        string="Account",
        required=True,
        ondelete="restrict",
        index=True,
    )
    to_phone = fields.Char(
        string="To (Phone)",
        required=True,
        help="E.164 formatted recipient number, e.g. +6281234567890.",
    )
    to_partner_id = fields.Many2one(
        "res.partner",
        string="Recipient",
        ondelete="set null",
        index=True,
    )
    body = fields.Text(required=True)
    purpose = fields.Selection(
        [
            ("otp", "OTP"),
            ("transactional", "Transactional"),
            ("marketing", "Marketing"),
        ],
        default="transactional",
        required=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("queued", "Queued"),
            ("sent", "Sent"),
            ("delivered", "Delivered"),
            ("failed", "Failed"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    error_message = fields.Text(readonly=True)
    sent_at = fields.Datetime(readonly=True)
    provider_message_id = fields.Char(
        string="Provider Message ID",
        readonly=True,
        help="Upstream message ID returned by the provider on accept.",
    )
    consent_verified = fields.Boolean(default=False, readonly=True)

    # ---------- public API ----------

    def action_send(self):
        """Verify PDP consent (hard-gate marketing), then dispatch via the adapter."""
        Consent = self.env["pdp.consent"]
        for rec in self:
            purpose_code = _PURPOSE_CONSENT_CODE.get(rec.purpose)

            consent_ok = True
            if purpose_code:
                if rec.to_partner_id:
                    consent_ok = Consent.check_consent(rec.to_partner_id, purpose_code)
                else:
                    # No partner linked — cannot verify; treat marketing as missing.
                    consent_ok = False

            if rec.purpose == "marketing" and not consent_ok:
                raise UserError(
                    _("Cannot send marketing SMS to %s: no active PDP consent for purpose 'sms_marketing'.")
                    % (rec.to_partner_id.display_name or rec.to_phone)
                )

            if not consent_ok:
                _logger.warning(
                    "custom.sms.message %s: sending %s without verified consent (purpose_code=%s)",
                    rec.id,
                    rec.purpose,
                    purpose_code,
                )

            rec.consent_verified = consent_ok

            # ----- Adapter dispatch (stubbed inside the provider adapters) -----
            adapter = self.env["custom.sms.adapter.base"]._get_for_account(rec.account_id)
            try:
                result = adapter.send(
                    rec.account_id,
                    rec.to_phone,
                    rec.body or "",
                    purpose=rec.purpose,
                )
            except Exception as e:
                _logger.exception("SMS adapter send failed for message %s", rec.id)
                rec.write(
                    {
                        "state": "failed",
                        "error_message": str(e),
                    }
                )
                continue

            if result.get("ok"):
                rec.write(
                    {
                        "state": "sent",
                        "sent_at": fields.Datetime.now(),
                        "provider_message_id": result.get("provider_message_id"),
                        "error_message": False,
                    }
                )
            else:
                rec.write(
                    {
                        "state": "failed",
                        "error_message": result.get("message") or "Unknown adapter error",
                    }
                )
        return True
