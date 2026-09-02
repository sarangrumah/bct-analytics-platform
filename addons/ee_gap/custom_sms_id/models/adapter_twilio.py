# -*- coding: utf-8 -*-
"""Twilio (global provider) SMS adapter — real HTTP send + status poll."""

from __future__ import annotations

import logging
import uuid

import requests

from odoo import _, api, models

_logger = logging.getLogger(__name__)


_TWILIO_BASE = "https://api.twilio.com/2010-04-01"


def _twilio_messages_url(account_sid: str) -> str:
    return f"{_TWILIO_BASE}/Accounts/{account_sid}/Messages.json"


def _twilio_message_status_url(account_sid: str, message_sid: str) -> str:
    return f"{_TWILIO_BASE}/Accounts/{account_sid}/Messages/{message_sid}.json"


# Map Twilio delivery status → our internal state vocabulary
_TWILIO_STATUS_MAP = {
    "queued": "queued",
    "accepted": "queued",
    "scheduled": "queued",
    "sending": "sent",
    "sent": "sent",
    "delivered": "delivered",
    "undelivered": "failed",
    "failed": "failed",
    "canceled": "failed",
}


class CustomSmsAdapterTwilio(models.AbstractModel):
    _name = "custom.sms.adapter.twilio"
    _inherit = "custom.sms.adapter.base"
    _description = "SMS Adapter — Twilio"

    @api.model
    def send(self, account, to_phone: str, body: str, *, purpose: str | None = None) -> dict:
        """Send SMS via Twilio Messages API.

        Sandbox mode: no real HTTP call; returns a fake SID.
        Production: POST form-encoded ``From/To/Body`` with HTTP Basic
        auth to ``/Accounts/{sid}/Messages.json``.
        """
        if not (account.account_sid and account.auth_token):
            return {
                "ok": False,
                "provider_message_id": None,
                "message": "Twilio account_sid/auth_token not configured.",
            }

        if account.sandbox_mode:
            fake_sid = f"SM_sandbox_{uuid.uuid4().hex[:24]}"
            _logger.info(
                "[custom_sms_id:twilio:sandbox] sid=%s sender=%s to=%s purpose=%s body=%s",
                fake_sid,
                account.sender_id,
                to_phone,
                purpose,
                (body or "")[:160],
            )
            return {
                "ok": True,
                "provider_message_id": fake_sid,
                "message": "Sandbox send (Twilio)",
            }

        url = _twilio_messages_url(account.account_sid)
        payload = {
            "From": account.sender_id or "",
            "To": to_phone,
            "Body": body or "",
        }
        auth = (account.account_sid, account.auth_token)

        try:
            resp = self.env["custom.sms.adapter.base"]._post(
                url,
                data=payload,
                auth=auth,
                timeout=30,
                account=account,
            )
        except Exception as e:
            _logger.warning("Twilio send failed for %s: %s", to_phone, e)
            return {
                "ok": False,
                "provider_message_id": None,
                "message": _("Twilio HTTP error: %s") % e,
            }

        try:
            body_json = resp.json()
        except ValueError:
            return {
                "ok": False,
                "provider_message_id": None,
                "message": _("Twilio returned non-JSON body: %s") % resp.text[:200],
            }

        sid = body_json.get("sid")
        status = body_json.get("status") or "queued"
        if not sid:
            return {
                "ok": False,
                "provider_message_id": None,
                "message": _("Twilio response missing sid: %s") % body_json,
            }
        return {
            "ok": True,
            "provider_message_id": sid,
            "message": _("Accepted by Twilio (status=%s)") % status,
        }

    @api.model
    def test_connection(self, account) -> dict:
        if not (account.account_sid and account.auth_token):
            return {"ok": False, "message": "Twilio account_sid/auth_token not configured."}
        if account.sandbox_mode:
            return {"ok": True, "message": "Twilio credentials present (sandbox — no live call)."}
        # Ping the account endpoint (read-only, no SMS sent).
        url = f"{_TWILIO_BASE}/Accounts/{account.account_sid}.json"
        try:
            resp = requests.get(url, auth=(account.account_sid, account.auth_token), timeout=15)
        except requests.RequestException as e:
            return {"ok": False, "message": _("Twilio probe failed: %s") % e}
        if resp.status_code == 200:
            return {"ok": True, "message": "Twilio credentials valid."}
        return {
            "ok": False,
            "message": _("Twilio probe HTTP %s: %s") % (resp.status_code, resp.text[:200]),
        }

    @api.model
    def poll_status(self, account, provider_message_id: str) -> dict:
        """GET the Twilio message resource and return mapped delivery state."""
        if not (account.account_sid and account.auth_token):
            return {"ok": False, "status": None, "message": "Twilio credentials missing."}
        if account.sandbox_mode:
            # Sandbox SIDs aren't real — assume delivered for visibility.
            return {"ok": True, "status": "delivered", "message": "Sandbox stub status."}

        url = _twilio_message_status_url(account.account_sid, provider_message_id)
        try:
            resp = requests.get(
                url,
                auth=(account.account_sid, account.auth_token),
                timeout=15,
            )
        except requests.RequestException as e:
            return {"ok": False, "status": None, "message": _("Twilio status fetch failed: %s") % e}
        if resp.status_code != 200:
            return {
                "ok": False,
                "status": None,
                "message": _("Twilio status HTTP %s: %s") % (resp.status_code, resp.text[:200]),
            }
        try:
            body_json = resp.json()
        except ValueError:
            return {"ok": False, "status": None, "message": "Twilio returned non-JSON status."}
        raw_status = (body_json.get("status") or "").lower()
        mapped = _TWILIO_STATUS_MAP.get(raw_status)
        return {
            "ok": True,
            "status": mapped,
            "raw_status": raw_status,
            "error_code": body_json.get("error_code"),
            "error_message": body_json.get("error_message"),
            "message": _("Twilio status: %s") % raw_status,
        }
