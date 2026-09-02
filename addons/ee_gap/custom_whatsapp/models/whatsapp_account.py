# -*- coding: utf-8 -*-
"""WhatsApp Cloud API account configuration (per-company) + HTTP adapter helpers.

The adapter follows the pattern used by ``custom_coretax_pajakku`` —
retry with exponential backoff up to 3 attempts, honour ``Retry-After``
on 429, and a per-account circuit breaker (10 consecutive failures
opens for 1 hour). Sandbox mode short-circuits real HTTP calls and
returns a synthetic message id so the rest of the pipeline can be
exercised without consuming Meta quota.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any

import requests

from odoo import _, fields, models
from odoo.exceptions import UserError


def _env_default_baileys_url() -> str:
    return os.environ.get("BAILEYS_INTERNAL_URL", "http://baileys:8088")


def _env_default_baileys_secret() -> str:
    return os.environ.get("BAILEYS_SHARED_SECRET", "")


_logger = logging.getLogger(__name__)


# Meta Graph API version. v22.0 is the GA version as of 2026.
META_GRAPH_VERSION = "v22.0"
META_GRAPH_BASE = f"https://graph.facebook.com/{META_GRAPH_VERSION}"

# ----- Circuit breaker state (module-level, per-account) -----
_CB_STATE: dict[int, dict[str, float]] = {}
_CB_THRESHOLD = 10
_CB_OPEN_SECONDS = 3600

# Retry policy
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds; doubles each attempt
_HTTP_TIMEOUT = 30


def _now() -> float:
    return time.monotonic()


def _circuit_open(account_id: int) -> bool:
    st = _CB_STATE.get(account_id)
    return bool(st and _now() < st.get("open_until", 0))


def _circuit_record_success(account_id: int) -> None:
    _CB_STATE.pop(account_id, None)


def _circuit_record_failure(account_id: int) -> bool:
    st = _CB_STATE.setdefault(account_id, {"fail_streak": 0, "open_until": 0})
    st["fail_streak"] += 1
    if st["fail_streak"] >= _CB_THRESHOLD:
        st["open_until"] = _now() + _CB_OPEN_SECONDS
        return True
    return False


class WhatsappAccount(models.Model):
    _name = "whatsapp.account"
    _description = "WhatsApp Account"
    _order = "name"

    name = fields.Char(required=True)
    provider = fields.Selection(
        [
            ("meta_cloud", "Meta WhatsApp Cloud API"),
            ("twilio", "Twilio WhatsApp"),
            ("baileys", "Baileys (WhatsApp Web sidecar)"),
        ],
        default="meta_cloud",
        required=True,
    )
    phone_number_id = fields.Char(
        string="Phone Number ID",
        help="Meta Cloud API phone_number_id used as the from-address for outbound messages.",
    )
    business_account_id = fields.Char(
        string="WhatsApp Business Account ID",
        help="Meta WABA ID owning the phone number and approved templates.",
    )
    access_token = fields.Char(
        string="Access Token",
        groups="custom_whatsapp.group_manager",
        help=(
            "System User access token (Meta Cloud) or Auth Token (Twilio). "
            "Stored as plain Char in this phase — move to custom.ir.config encrypted "
            "storage before production rollout."
        ),
    )
    webhook_verify_token = fields.Char(
        string="Webhook Verify Token",
        groups="custom_whatsapp.group_manager",
        help="Shared secret presented by Meta when verifying the webhook callback URL.",
    )
    is_active = fields.Boolean(default=True)
    sandbox_mode = fields.Boolean(
        default=True,
        help="When enabled, outbound sends are stubbed/logged instead of hitting the live Graph API.",
    )
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        required=True,
        index=True,
    )

    # ----- Baileys sidecar fields (provider == 'baileys') -----
    baileys_sidecar_url = fields.Char(
        string="Baileys Sidecar URL",
        default=lambda self: _env_default_baileys_url(),
        help=(
            "Base URL of the Baileys Node.js sidecar, default http://baileys:8088 "
            "(internal docker network hostname). Auto-prefilled from the "
            "BAILEYS_INTERNAL_URL environment variable when the record is created."
        ),
    )
    baileys_shared_secret = fields.Char(
        string="Baileys Shared Secret",
        default=lambda self: _env_default_baileys_secret(),
        groups="custom_whatsapp.group_manager",
        help=(
            "Bearer token presented to the sidecar AND used to validate the HMAC on "
            "inbound webhooks. Must match BAILEYS_SHARED_SECRET on the baileys "
            "service exactly. Auto-prefilled from that env var on create."
        ),
    )
    baileys_session_id = fields.Char(
        string="Baileys Session ID",
        help=(
            "Logical session name inside the sidecar (one socket per session). "
            "Leave blank to auto-assign acct-{id} on first Start Session."
        ),
    )
    baileys_status = fields.Selection(
        [
            ("unknown", "Unknown"),
            ("qr_pending", "QR Pairing Pending"),
            ("connecting", "Connecting"),
            ("connected", "Connected"),
            ("disconnected", "Disconnected"),
            ("error", "Error"),
        ],
        default="unknown",
        readonly=True,
    )
    baileys_last_qr = fields.Binary(
        string="Pairing QR",
        readonly=True,
        help="Latest QR PNG fetched from the sidecar — clear once paired.",
    )
    baileys_phone = fields.Char(readonly=True, help="MSISDN reported by the sidecar after pairing.")
    baileys_last_error = fields.Text(readonly=True)

    # ----- AI draft reply (per-account persona) -----
    ai_system_prompt = fields.Text(
        string="AI System Prompt",
        help=(
            "Persona and tone instructions for AI-generated draft replies on inbound "
            "WhatsApp messages. Example: 'Kamu customer service Sarang Rumah yang "
            "ramah, jawab singkat dalam Bahasa Indonesia, arahkan ke katalog kalau "
            "pelanggan tanya produk.' Leave blank to disable AI drafts even when "
            "Auto-Draft is on."
        ),
    )
    ai_auto_draft = fields.Boolean(
        string="Auto-Draft AI Reply",
        default=False,
        help=(
            "When enabled, every inbound message triggers an AI draft reply stored on "
            "the message. Nothing is sent automatically — the agent must review and "
            "click Send. Requires ai_system_prompt to be set."
        ),
    )
    ai_max_history = fields.Integer(
        string="AI History Window",
        default=10,
        help="Number of recent messages with the same contact to send as context to the AI.",
    )

    # ----- API URL helpers -----

    def _get_api_url(self, endpoint: str | None = None) -> str:
        """Build the Cloud API URL for a given endpoint on this account.

        ``endpoint`` is the resource appended to the phone-number path,
        e.g. ``"messages"`` -> ``.../{phone_number_id}/messages``.
        If ``endpoint`` is None, returns the phone-number base URL.
        """
        self.ensure_one()
        if not self.phone_number_id:
            raise UserError(_("WhatsApp account '%s' has no phone_number_id configured.") % self.name)
        base = f"{META_GRAPH_BASE}/{self.phone_number_id}"
        return f"{base}/{endpoint}" if endpoint else base

    def _get_waba_url(self, endpoint: str) -> str:
        """Build a WhatsApp Business Account scoped URL."""
        self.ensure_one()
        if not self.business_account_id:
            raise UserError(_("WhatsApp account '%s' has no business_account_id configured.") % self.name)
        return f"{META_GRAPH_BASE}/{self.business_account_id}/{endpoint}"

    def _get_headers(self) -> dict[str, str]:
        self.ensure_one()
        token = self.sudo().access_token or ""
        if not token and not self.sandbox_mode:
            raise UserError(_("WhatsApp account '%s' has no access_token configured.") % self.name)
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ----- HTTP -----

    def _request(
        self, method: str, url: str, *, json_body: dict | None = None, params: dict | None = None
    ) -> dict[str, Any]:
        """Perform an HTTP request with retry + circuit breaker.

        Returns the parsed JSON body on success. Raises ``RuntimeError``
        with a sanitised message on failure (access_token is never
        included in the exception text).
        """
        self.ensure_one()
        request_id = uuid.uuid4().hex[:8]

        if _circuit_open(self.id):
            raise RuntimeError(
                f"WhatsApp circuit breaker OPEN for account '{self.name}' (req={request_id}). Auto-resets in ~1h."
            )

        headers = self._get_headers()
        attempt = 0
        last_exc: Exception | None = None
        t0 = time.monotonic()

        while attempt < _MAX_RETRIES:
            attempt += 1
            try:
                # CodeQL py/clear-text-logging-sensitive-data: the URL is the
                # WABA Graph endpoint (e.g. /v19.0/<phone_id>/messages). The
                # bearer token lives in the Authorization header (see
                # _get_headers above), never in the URL. Log the URL with no
                # query string just to be doubly defensive.
                url_for_log = url.split("?", 1)[0]
                _logger.info(
                    "[whatsapp http] req=%s account=%s attempt=%s %s %s",
                    request_id,
                    self.name,
                    attempt,
                    method,
                    url_for_log,  # lgtm[py/clear-text-logging-sensitive-data]
                )
                resp = requests.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    params=params,
                    timeout=_HTTP_TIMEOUT,
                )
                latency_ms = int((time.monotonic() - t0) * 1000)

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    if attempt < _MAX_RETRIES:
                        _logger.warning(
                            "[whatsapp http] req=%s 429 received, sleeping %ss",
                            request_id,
                            retry_after,
                        )
                        time.sleep(min(retry_after, 30))
                        continue

                if resp.status_code >= 500 and attempt < _MAX_RETRIES:
                    _logger.warning(
                        "[whatsapp http] req=%s status=%s, retrying",
                        request_id,
                        resp.status_code,
                    )
                    time.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))
                    continue

                if resp.status_code >= 400:
                    # Don't include token in error text. resp.text is OK; Meta
                    # error bodies do not echo Authorization.
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:400]}")

                _circuit_record_success(self.id)
                _logger.info(
                    "[whatsapp http] req=%s ok status=%s latency=%sms",
                    request_id,
                    resp.status_code,
                    latency_ms,
                )
                try:
                    return resp.json() if resp.content else {}
                except ValueError:
                    return {"raw": resp.text}

            except requests.RequestException as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))
                    continue
                break

        tripped = _circuit_record_failure(self.id)
        if tripped:
            _logger.error(
                "[whatsapp http] req=%s circuit OPENED for account=%s",
                request_id,
                self.name,
            )
        raise RuntimeError(
            f"WhatsApp request failed after {_MAX_RETRIES} attempts "
            f"(req={request_id}): {last_exc or 'see prior log line'}"
        )

    def _post(self, endpoint: str, payload: dict) -> dict[str, Any]:
        """POST a JSON payload to ``{phone_number_id}/{endpoint}``."""
        self.ensure_one()
        return self._request("POST", self._get_api_url(endpoint), json_body=payload)

    def _get(self, url: str, params: dict | None = None) -> dict[str, Any]:
        """GET an arbitrary Graph URL (used for WABA template polling)."""
        self.ensure_one()
        return self._request("GET", url, params=params)

    # ----- Baileys HTTP helpers -----

    def _baileys_headers(self) -> dict[str, str]:
        self.ensure_one()
        secret = self.sudo().baileys_shared_secret or ""
        if not secret:
            raise UserError(_("Baileys shared secret is not configured for account '%s'.") % self.name)
        return {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _baileys_url(self, path: str) -> str:
        self.ensure_one()
        base = (self.baileys_sidecar_url or "").rstrip("/")
        if not base:
            raise UserError(_("Baileys sidecar URL is not configured for account '%s'.") % self.name)
        return f"{base}/{path.lstrip('/')}"

    def _baileys_request(
        self, method: str, path: str, *, json_body: dict | None = None, params: dict | None = None
    ) -> dict[str, Any]:
        """Sidecar HTTP call with retry + circuit breaker.

        Re-uses the same circuit breaker namespace as Meta so a flaky
        sidecar doesn't get drowned by retries. Sandbox short-circuit
        is handled by the caller (see :meth:`whatsapp.message._do_send`).
        """
        self.ensure_one()
        request_id = uuid.uuid4().hex[:8]

        if _circuit_open(self.id):
            raise RuntimeError(f"Baileys circuit breaker OPEN for account '{self.name}' (req={request_id}).")

        url = self._baileys_url(path)
        headers = self._baileys_headers()
        attempt = 0
        last_exc: Exception | None = None
        t0 = time.monotonic()
        while attempt < _MAX_RETRIES:
            attempt += 1
            try:
                url_for_log = url.split("?", 1)[0]
                _logger.info(
                    "[baileys http] req=%s account=%s attempt=%s %s %s",
                    request_id,
                    self.name,
                    attempt,
                    method,
                    url_for_log,
                )
                resp = requests.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    params=params,
                    timeout=_HTTP_TIMEOUT,
                )
                latency_ms = int((time.monotonic() - t0) * 1000)
                if resp.status_code >= 500 and attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))
                    continue
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:400]}")
                _circuit_record_success(self.id)
                _logger.info(
                    "[baileys http] req=%s ok status=%s latency=%sms",
                    request_id,
                    resp.status_code,
                    latency_ms,
                )
                try:
                    return resp.json() if resp.content else {}
                except ValueError:
                    return {"raw": resp.text}
            except requests.RequestException as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))
                    continue
                break

        tripped = _circuit_record_failure(self.id)
        if tripped:
            _logger.error(
                "[baileys http] req=%s circuit OPENED for account=%s",
                request_id,
                self.name,
            )
        raise RuntimeError(
            f"Baileys request failed after {_MAX_RETRIES} attempts "
            f"(req={request_id}): {last_exc or 'see prior log line'}"
        )

    def _baileys_post(self, path: str, payload: dict | None = None) -> dict[str, Any]:
        self.ensure_one()
        return self._baileys_request("POST", path, json_body=payload or {})

    def _baileys_get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        self.ensure_one()
        return self._baileys_request("GET", path, params=params)

    # ----- Baileys UI actions -----

    def _baileys_session(self) -> str:
        self.ensure_one()
        return self.baileys_session_id or f"acct-{self.id}"

    def action_baileys_start_session(self):
        self.ensure_one()
        secret = self.sudo().baileys_shared_secret or ""
        try:
            body = self._baileys_post(
                f"sessions/{self._baileys_session()}/start",
                {"account_id": self.id, "hmac_secret": secret},
            )
            status = body.get("status") or "unknown"
            self.write(
                {
                    "baileys_status": status
                    if status in {"qr_pending", "connecting", "connected", "disconnected", "error"}
                    else "unknown",
                    "baileys_last_error": False,
                }
            )
            return self.action_baileys_refresh_qr()
        except Exception as e:
            self.write({"baileys_status": "error", "baileys_last_error": str(e)[:2000]})
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {"title": _("Baileys start failed"), "message": str(e), "type": "danger", "sticky": True},
            }

    def action_baileys_refresh_qr(self):
        self.ensure_one()
        try:
            status = self._baileys_get(f"sessions/{self._baileys_session()}/status")
        except Exception as e:
            self.write({"baileys_status": "error", "baileys_last_error": str(e)[:2000]})
            return False
        vals = {
            "baileys_status": status.get("status") or "unknown",
            "baileys_phone": status.get("phone") or False,
            "baileys_last_error": status.get("last_error") or False,
        }
        if status.get("has_qr"):
            try:
                qr = self._baileys_get(
                    f"sessions/{self._baileys_session()}/qr",
                    params={"format": "base64"},
                )
                vals["baileys_last_qr"] = qr.get("png_base64") or False
            except Exception as e:
                _logger.warning("baileys QR fetch failed: %s", e)
        elif vals["baileys_status"] == "connected":
            vals["baileys_last_qr"] = False
        self.write(vals)
        return True

    def action_baileys_logout(self):
        self.ensure_one()
        try:
            self._baileys_post(f"sessions/{self._baileys_session()}/logout")
        except Exception as e:
            _logger.warning("baileys logout error: %s", e)
        self.write(
            {
                "baileys_status": "disconnected",
                "baileys_last_qr": False,
                "baileys_phone": False,
            }
        )
        return True

    # ----- UI action: test connection -----

    def action_test_connection(self):
        """Smoke-test: GET the phone-number resource to validate token + id."""
        self.ensure_one()
        if self.sandbox_mode:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Sandbox Mode"),
                    "message": _("Account is in sandbox mode — no real HTTP call performed."),
                    "type": "info",
                    "sticky": False,
                },
            }
        try:
            body = self._get(self._get_api_url())
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Connection OK"),
                    "message": _("Phone number resource reachable: %s") % body.get("id", "n/a"),
                    "type": "success",
                    "sticky": False,
                },
            }
        except Exception as e:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Connection Failed"),
                    "message": str(e),
                    "type": "danger",
                    "sticky": True,
                },
            }
