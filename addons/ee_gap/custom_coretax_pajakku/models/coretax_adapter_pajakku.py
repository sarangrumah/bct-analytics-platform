# -*- coding: utf-8 -*-
"""Concrete Pajakku adapter (host-to-host via the ASPP REST API).

OAuth2 client-credentials flow → access token cached in-process for
``expires_in`` seconds. Token refresh on 401. Retries with exponential
backoff up to 3 attempts; honours ``Retry-After`` on 429. Circuit
breaker: 10 consecutive failures → adapter disabled for 1 hour and an
ops alert is posted to the company's mail thread.

The actual Pajakku API endpoints are placeholders until live sandbox
credentials are configured per the locked decision (no mock server).
When ``pajakku_enabled = False`` the adapter refuses to send and points
the operator to the manual flow.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


# ----- Module-level cache -----
# Token cache: {tenant_db: {"token": str, "expires_at": float}}
_TOKEN_CACHE: dict[str, dict[str, Any]] = {}

# Circuit breaker state: {company_id: {"fail_streak": int, "open_until": float}}
_CB_STATE: dict[int, dict[str, float]] = {}
_CB_THRESHOLD = 10  # consecutive failures before opening
_CB_OPEN_SECONDS = 3600  # circuit stays open for 1 hour

# Retry policy
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds; doubles each attempt


# ============================================================
# Helpers
# ============================================================


def _now() -> float:
    return time.monotonic()


def _circuit_open(company_id: int) -> bool:
    st = _CB_STATE.get(company_id)
    if not st:
        return False
    return _now() < st.get("open_until", 0)


def _circuit_record_success(company_id: int) -> None:
    _CB_STATE.pop(company_id, None)


def _circuit_record_failure(company_id: int) -> bool:
    """Return True if the failure tripped the breaker open."""
    st = _CB_STATE.setdefault(company_id, {"fail_streak": 0, "open_until": 0})
    st["fail_streak"] += 1
    if st["fail_streak"] >= _CB_THRESHOLD:
        st["open_until"] = _now() + _CB_OPEN_SECONDS
        return True
    return False


# ============================================================
# Adapter
# ============================================================


class CoretaxAdapterBaseExtend(models.AbstractModel):
    """Register 'pajakku' in the dispatcher mapping."""

    _inherit = "custom.coretax.adapter.base"

    @api.model
    def _get_for_config(self, config):
        if getattr(config, "adapter_type", None) == "pajakku":
            return self.env["custom.coretax.adapter.pajakku"]
        return super()._get_for_config(config)


class CoretaxAdapterPajakku(models.AbstractModel):
    _name = "custom.coretax.adapter.pajakku"
    _inherit = "custom.coretax.adapter.base"
    _description = "Coretax Pajakku Adapter (host-to-host)"

    # -------- Public API (overrides) --------

    @api.model
    def submit_xml(
        self, xml_bytes: bytes, *, config=None, transaction_type: str | None = None, source_record=None
    ) -> dict:
        config = config or self._resolve_config()
        self._guard_enabled(config)
        if _circuit_open(config.company_id.id):
            raise UserError(
                _(
                    "Pajakku circuit breaker is OPEN for company '%s'. Will auto-reset in "
                    "~1 hour, or fix the underlying error and retry manually."
                )
                % config.company_id.name
            )

        # Materialise a transaction row up front so we have something to update on failure
        tx = (
            self.env["custom.coretax.transaction"]
            .sudo()
            .create(
                {
                    "company_id": config.company_id.id,
                    "config_id": config.id,
                    "transaction_type": transaction_type or "efaktur_keluaran",
                    "account_move_id": source_record.id
                    if source_record and source_record._name == "account.move"
                    else False,
                    "bukti_potong_id": source_record.id
                    if source_record and source_record._name == "custom.coretax.bukti.potong"
                    else False,
                    "payload": base64.b64encode(xml_bytes),
                    "payload_filename": "submit.xml",
                }
            )
        )
        tx.mark_submitting()

        try:
            response = self._http_post(
                config,
                path=self._endpoint_for_submit(transaction_type),
                files={
                    "xml": ("submit.xml", xml_bytes, "application/xml"),
                },
            )
            body = response.json() if response.content else {}
            uuid = body.get("submission_uuid") or body.get("uuid") or body.get("id")
            if not uuid:
                raise RuntimeError(f"Pajakku response missing submission UUID: {body!r}")
            tx.mark_submitted(uuid, response_xml=response.content)
            _circuit_record_success(config.company_id.id)
            self._bump_usage(config, "faktur_submits" if "efaktur" in (transaction_type or "") else "bupot_submits")
            return {
                "submission_uuid": uuid,
                "status": "submitted",
                "message": body.get("message", "Submitted to Pajakku"),
                "transaction_id": tx.id,
            }
        except Exception as e:
            tx.mark_error(str(e))
            self._bump_usage(config, "errors")
            tripped = _circuit_record_failure(config.company_id.id)
            if tripped:
                self._notify_breaker_open(config)
            raise UserError(_("Pajakku submit failed: %s") % e) from e

    @api.model
    def query_nsfp(self, submission_uuid: str, *, config=None) -> str | None:
        config = config or self._resolve_config()
        self._guard_enabled(config)
        try:
            response = self._http_get(config, path=f"/api/v1/efaktur/{submission_uuid}/status")
            body = response.json()
            status = body.get("status")
            if status == "approved":
                nsfp = body.get("nsfp") or body.get("nomor_faktur")
                tx = (
                    self.env["custom.coretax.transaction"]
                    .sudo()
                    .search([("external_uuid", "=", submission_uuid)], limit=1)
                )
                if tx and nsfp:
                    tx.mark_approved(nsfp, response_pdf=None)
                return nsfp
            if status == "rejected":
                tx = (
                    self.env["custom.coretax.transaction"]
                    .sudo()
                    .search([("external_uuid", "=", submission_uuid)], limit=1)
                )
                if tx:
                    tx.mark_rejected(body.get("code", "REJECT"), body.get("message", ""))
                return None
            return None  # still in progress
        except Exception as e:
            _logger.warning("Pajakku query_nsfp failed for %s: %s", submission_uuid, e)
            return None

    @api.model
    def download_response(self, submission_uuid: str, *, config=None) -> bytes:
        config = config or self._resolve_config()
        self._guard_enabled(config)
        response = self._http_get(
            config,
            path=f"/api/v1/efaktur/{submission_uuid}/response",
            stream=True,
        )
        return response.content

    # -------- Test connection (called from config form button) --------

    @api.model
    def test_connection(self, config) -> dict:
        """Perform an OAuth2 token exchange and return status info."""
        if not (config.pajakku_client_id and config.pajakku_client_secret_set):
            return {"ok": False, "message": _("Pajakku credentials not configured.")}
        try:
            token = self._get_token(config, force_refresh=True)
            return {
                "ok": True,
                "message": _("OAuth2 token obtained (length: %s).") % len(token),
                "sandbox": config.pajakku_sandbox_mode,
            }
        except Exception as e:
            return {"ok": False, "message": str(e)}

    # -------- Internal: HTTP machinery --------

    def _resolve_config(self):
        config = (
            self.env["custom.coretax.config"]
            .sudo()
            .search(
                [("active", "=", True), ("company_id", "in", (False, self.env.company.id))],
                limit=1,
            )
        )
        if not config:
            raise UserError(_("No active Coretax config for this company."))
        return config

    def _guard_enabled(self, config):
        if not getattr(config, "pajakku_enabled", False):
            raise UserError(
                _(
                    "Pajakku adapter is disabled for company '%s'. "
                    "Enable in Coretax Config or switch adapter_type to manual."
                )
                % config.company_id.name
            )
        if not (config.pajakku_client_id and config.pajakku_client_secret_set):
            raise UserError(
                _("Pajakku credentials missing. Open Coretax Config → Pajakku tab to set client_id and client_secret.")
            )

    def _base_url(self, config) -> str:
        if config.pajakku_api_url:
            return config.pajakku_api_url.rstrip("/")
        return "https://sandbox-api.pajakku.com" if config.pajakku_sandbox_mode else "https://api.pajakku.com"

    def _endpoint_for_submit(self, transaction_type: str | None) -> str:
        ttype = transaction_type or ""
        if ttype.startswith("efaktur"):
            return "/api/v1/efaktur/submit"
        if ttype.startswith("bupot"):
            return "/api/v1/bupot/submit"
        return "/api/v1/coretax/submit"

    def _get_token(self, config, force_refresh: bool = False) -> str:
        cache_key = self.env.cr.dbname
        cached = _TOKEN_CACHE.get(cache_key)
        if not force_refresh and cached and cached.get("expires_at", 0) > _now() + 30:
            return cached["token"]

        secret = config._pajakku_get_client_secret()
        if not secret:
            raise RuntimeError("Pajakku client_secret could not be decrypted from config.")

        url = f"{self._base_url(config)}/oauth/token"
        try:
            resp = requests.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": config.pajakku_client_id,
                    "client_secret": secret,
                    "scope": "efaktur:write bupot:write",
                },
                timeout=30,
            )
        except requests.RequestException as e:
            raise RuntimeError(f"OAuth2 transport error: {e}") from e

        if resp.status_code != 200:
            raise RuntimeError(f"OAuth2 failed: HTTP {resp.status_code} — {resp.text[:200]}")

        body = resp.json()
        token = body.get("access_token")
        ttl = int(body.get("expires_in", 3600))
        if not token:
            raise RuntimeError(f"OAuth2 response missing access_token: {body!r}")
        _TOKEN_CACHE[cache_key] = {"token": token, "expires_at": _now() + ttl}
        return token

    def _http_request(self, method: str, config, path: str, **kwargs) -> requests.Response:
        url = f"{self._base_url(config)}{path}"
        attempt = 0
        last_exc: Exception | None = None
        while attempt < _MAX_RETRIES:
            attempt += 1
            self._bump_usage(config, "api_calls")
            try:
                token = self._get_token(config, force_refresh=(attempt > 1))
                headers = dict(kwargs.pop("headers", {}))
                headers["Authorization"] = f"Bearer {token}"
                headers.setdefault("Accept", "application/json")
                resp = requests.request(method, url, headers=headers, timeout=60, **kwargs)
                if resp.status_code == 401 and attempt < _MAX_RETRIES:
                    # Token may be stale — force refresh and retry once
                    continue
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    if attempt < _MAX_RETRIES:
                        time.sleep(min(retry_after, 30))
                        continue
                if resp.status_code >= 500 and attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))
                    continue
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                return resp
            except requests.RequestException as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))
                    continue
                raise
            except Exception as e:
                last_exc = e
                raise
        raise RuntimeError(f"All {_MAX_RETRIES} attempts failed; last: {last_exc}")

    def _http_post(self, config, path: str, **kwargs) -> requests.Response:
        return self._http_request("POST", config, path, **kwargs)

    def _http_get(self, config, path: str, **kwargs) -> requests.Response:
        return self._http_request("GET", config, path, **kwargs)

    # -------- Usage + alerts --------

    def _bump_usage(self, config, kind: str):
        try:
            self.env["custom.coretax.pajakku.usage"].sudo().increment(kind, company=config.company_id)
        except Exception:
            _logger.debug("usage bump failed for %s", kind, exc_info=True)

    def _notify_breaker_open(self, config):
        try:
            config.message_post(
                body=_(
                    "<b>Pajakku circuit breaker OPENED</b> after %s consecutive failures. "
                    "Submissions will be refused for ~1 hour. Investigate Pajakku status, "
                    "credentials, or last_error on recent transactions."
                )
                % _CB_THRESHOLD,
                subtype_xmlid="mail.mt_note",
            )
        except Exception:
            _logger.exception("Failed to post breaker notification")

    # -------- Cron-driven sync --------

    @api.model
    def _cron_poll_pending(self):
        """Called every 30 minutes to advance in-flight submissions."""
        Tx = self.env["custom.coretax.transaction"].sudo()
        pending = Tx.search([("state", "=", "submitted")])
        for tx in pending:
            try:
                config = tx.config_id
                if not config.pajakku_enabled:
                    continue
                self.query_nsfp(tx.external_uuid, config=config)
                tx.write({"last_polled_at": fields.Datetime.now()})
            except Exception:
                _logger.exception("poll failed for tx %s", tx.id)

        # Also retry queued errored items (subject to circuit breaker)
        retryable = Tx.search([("state", "=", "queued"), ("retry_count", "<", _MAX_RETRIES)])
        for tx in retryable:
            try:
                if not tx.payload:
                    continue
                config = tx.config_id
                xml_bytes = base64.b64decode(tx.payload)
                self.submit_xml(
                    xml_bytes,
                    config=config,
                    transaction_type=tx.transaction_type,
                    source_record=tx.account_move_id or tx.bukti_potong_id or None,
                )
            except Exception:
                _logger.exception("retry submit failed for tx %s", tx.id)
