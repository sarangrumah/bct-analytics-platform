# -*- coding: utf-8 -*-
"""Zenziva (Indonesia local provider) SMS adapter — real HTTP send."""

from __future__ import annotations

import logging
import uuid
from urllib.parse import urlparse

from odoo import _, api, models

_logger = logging.getLogger(__name__)


_ZENZIVA_PROD_URL = "https://console.zenziva.net/reguler/api/sendsms/"


class CustomSmsAdapterZenziva(models.AbstractModel):
    _name = "custom.sms.adapter.zenziva"
    _inherit = "custom.sms.adapter.base"
    _description = "SMS Adapter — Zenziva (Indonesia)"

    @api.model
    def send(self, account, to_phone: str, body: str, *, purpose: str | None = None) -> dict:
        """Send SMS via Zenziva regular endpoint.

        Sandbox mode: no real HTTP call; returns a fake message id.
        Production: POST form-encoded ``userkey/passkey/nohp/pesan`` to
        ``https://console.zenziva.net/reguler/api/sendsms/``.
        """
        if not (account.userkey and account.passkey):
            return {
                "ok": False,
                "provider_message_id": None,
                "message": "Zenziva userkey/passkey not configured.",
            }

        if account.sandbox_mode:
            fake_id = f"zenziva_sandbox_{uuid.uuid4().hex[:12]}"
            _logger.info(
                "[custom_sms_id:zenziva:sandbox] sender=%s to=%s purpose=%s body=%s id=%s",
                account.sender_id,
                to_phone,
                purpose,
                (body or "")[:160],
                fake_id,
            )
            return {
                "ok": True,
                "provider_message_id": fake_id,
                "message": "Sandbox send (Zenziva)",
            }

        endpoint = (account.api_url or _ZENZIVA_PROD_URL).rstrip("/")
        # Allow operator to override only the base; if they supplied
        # the bare base host, append the standard path. We parse the URL and
        # compare the netloc exactly — a plain `"console.zenziva.net" in
        # endpoint` would also accept e.g. `https://attacker.com/console.zenziva.net/`
        # (CodeQL py/incomplete-url-substring-sanitization).
        if not endpoint.endswith("/sendsms"):
            if urlparse(endpoint).netloc == "console.zenziva.net" and "/reguler/api/sendsms" not in endpoint:
                endpoint = _ZENZIVA_PROD_URL

        payload = {
            "userkey": account.userkey,
            "passkey": account.passkey,
            "nohp": to_phone,
            "pesan": body or "",
        }

        try:
            resp = self.env["custom.sms.adapter.base"]._post(
                endpoint,
                data=payload,
                timeout=30,
                account=account,
            )
        except Exception as e:
            _logger.warning("Zenziva send failed for %s: %s", to_phone, e)
            return {
                "ok": False,
                "provider_message_id": None,
                "message": _("Zenziva HTTP error: %s") % e,
            }

        # Parse response. Zenziva returns JSON shaped like:
        # {"status":"1","text":"Success","messageid":"..."}
        try:
            body_json = resp.json()
        except ValueError:
            return {
                "ok": False,
                "provider_message_id": None,
                "message": _("Zenziva returned non-JSON body: %s") % resp.text[:200],
            }

        # Some Zenziva variants nest under "data" / "messagedata"
        data = body_json
        if isinstance(body_json.get("data"), dict):
            data = body_json["data"]
        elif isinstance(body_json.get("messagedata"), list) and body_json["messagedata"]:
            data = body_json["messagedata"][0]

        status = str(data.get("status", body_json.get("status", ""))).strip()
        message_id = (
            data.get("messageId") or data.get("messageid") or body_json.get("messageId") or body_json.get("messageid")
        )
        text = data.get("text") or body_json.get("text") or ""

        if status == "1":
            return {
                "ok": True,
                "provider_message_id": message_id or None,
                "message": text or "Sent",
            }
        return {
            "ok": False,
            "provider_message_id": message_id or None,
            "message": _("Zenziva rejected: status=%s text=%s") % (status, text),
        }

    @api.model
    def test_connection(self, account) -> dict:
        if not (account.userkey and account.passkey):
            return {"ok": False, "message": "Zenziva userkey/passkey not configured."}
        if account.sandbox_mode:
            return {"ok": True, "message": "Zenziva credentials present (sandbox — no live call)."}
        # In production, do not waste an SMS for a probe; just confirm creds.
        return {"ok": True, "message": "Zenziva credentials configured (production)."}

    @api.model
    def poll_status(self, account, provider_message_id: str) -> dict:
        """Zenziva regular API does not expose a status endpoint.

        Delivery reports arrive via DLR webhook (out of scope here).
        Return ``ok=False, status=None`` so the cron can skip cleanly.
        """
        return {
            "ok": False,
            "status": None,
            "message": "Zenziva DLR polling not supported (webhook-only).",
        }
