# -*- coding: utf-8 -*-
"""Indonesia payment gateway adapter base.

HTTP machinery shared by MidtransAdapter / XenditAdapter / DokuAdapter:

- Retry with exponential backoff up to ``_MAX_RETRIES`` attempts.
- ``Retry-After`` honoured on HTTP 429.
- Per-provider circuit breaker: ``_CB_THRESHOLD`` consecutive failures
  open the breaker for ``_CB_OPEN_SECONDS``; further sends short-circuit
  and raise immediately until the cool-down elapses.
- Every send is materialised as a ``custom.payment.id.log`` row so ops
  can replay/inspect from the UI.

Pattern lifted from ``custom_coretax_pajakku.coretax_adapter_pajakku``
to keep the platform's outbound-call surface consistent.
"""

from __future__ import annotations

import json
import logging
import time

import requests

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


# ----- Module-level state -----
# Circuit breaker keyed by (db, provider_id) so one bad provider on one
# tenant does not block others.
_CB_STATE: dict[tuple[str, int], dict[str, float]] = {}
_CB_THRESHOLD = 10
_CB_OPEN_SECONDS = 3600

_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0
_DEFAULT_TIMEOUT = 30


def _now() -> float:
    return time.monotonic()


def _cb_key(env, provider) -> tuple[str, int]:
    return (env.cr.dbname, provider.id)


def _circuit_open(env, provider) -> bool:
    st = _CB_STATE.get(_cb_key(env, provider))
    if not st:
        return False
    return _now() < st.get("open_until", 0)


def _circuit_record_success(env, provider) -> None:
    _CB_STATE.pop(_cb_key(env, provider), None)


def _circuit_record_failure(env, provider) -> bool:
    """Return True if the failure tripped the breaker open."""
    key = _cb_key(env, provider)
    st = _CB_STATE.setdefault(key, {"fail_streak": 0, "open_until": 0})
    st["fail_streak"] += 1
    if st["fail_streak"] >= _CB_THRESHOLD:
        st["open_until"] = _now() + _CB_OPEN_SECONDS
        return True
    return False


def _circuit_reset(env, provider) -> None:
    """Force-reset the breaker (testing / ops button)."""
    _CB_STATE.pop(_cb_key(env, provider), None)


class IdPaymentAdapter(models.AbstractModel):
    """Abstract base for Indonesia payment gateway HTTP adapters.

    Concrete subclasses override ``_base_url`` and ``_endpoint`` and may
    override ``_auth_headers``. They invoke :meth:`send` to dispatch a
    request through the shared retry / circuit-breaker / logging path.
    """

    _name = "custom.payment.id.adapter.base"
    _description = "Indonesia Payment Gateway Adapter (abstract base)"

    # -------- Subclass dispatch --------

    @api.model
    def _get_for_provider(self, provider):
        """Return the concrete adapter model for the given payment.provider."""
        code = provider.code
        mapping = {
            "midtrans": "custom.payment.id.adapter.midtrans",
            "xendit": "custom.payment.id.adapter.xendit",
            "doku": "custom.payment.id.adapter.doku",
            "pos": "custom.payment.id.adapter.pos",
        }
        model_name = mapping.get(code)
        if not model_name:
            raise UserError(_("No Indonesia adapter registered for provider code '%s'.") % code)
        return self.env[model_name]

    # -------- Subclass overrides (defaults raise) --------

    def _base_url(self, provider) -> str:
        raise NotImplementedError("Adapter must implement _base_url(provider)")

    def _endpoint(self, provider, payload: dict) -> str:
        raise NotImplementedError("Adapter must implement _endpoint(provider, payload)")

    def _auth_headers(self, provider, body_bytes: bytes | None = None) -> dict[str, str]:
        return {"Accept": "application/json", "Content-Type": "application/json"}

    # -------- High-level API (Snap token / Invoice / Checkout) --------

    def create_checkout(self, provider, transaction) -> dict:
        """Subclass override returning ``{redirect_url, reference, raw}``.

        Default implementation raises ``NotImplementedError``.
        """
        raise NotImplementedError("Adapter must implement create_checkout()")

    def test_connection(self, provider) -> dict:
        """Lightweight connectivity check.

        Default implementation issues a minimal POST so the gateway
        returns a 4xx (auth/validation) rather than 5xx, proving DNS +
        TLS + creds plumbing works. Subclasses may override.
        """
        return self.send(provider, {"_ping": True})

    # -------- Public entry --------

    @api.model
    def send(
        self,
        provider,
        payload: dict,
        *,
        transaction=None,
        method: str = "POST",
        endpoint_override: str | None = None,
    ) -> dict:
        """Dispatch ``payload`` to ``provider``'s gateway.

        :param provider: ``payment.provider`` recordset (singleton).
        :param payload: JSON-serialisable dict body.
        :param transaction: optional ``payment.transaction`` to attach
            the log row to.
        :param method: HTTP verb (default POST).
        :param endpoint_override: when set, used instead of
            :meth:`_endpoint` (useful for refund/status sub-paths).
        :return: dict with keys ``ok``, ``http_status``, ``body``,
            ``latency_ms``, ``log_id``.
        """
        provider.ensure_one()
        if _circuit_open(self.env, provider):
            raise UserError(
                _(
                    "Payment provider '%s' circuit breaker is OPEN. Auto-resets in ~1h, "
                    "or fix the underlying error and clear it manually."
                )
                % provider.name
            )

        log = (
            self.env["custom.payment.id.log"]
            .sudo()
            .create(
                {
                    "provider_id": provider.id,
                    "transaction_id": transaction.id if transaction else False,
                    "request_payload": json.dumps(payload, ensure_ascii=False, default=str),
                    "state": "queued",
                }
            )
        )

        endpoint = endpoint_override or self._endpoint(provider, payload)
        url = f"{self._base_url(provider).rstrip('/')}{endpoint}"
        body_bytes = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        headers = self._auth_headers(provider, body_bytes=body_bytes)
        attempt = 0
        last_exc: Exception | None = None
        started = time.monotonic()
        while attempt < _MAX_RETRIES:
            attempt += 1
            log.write({"state": "sent", "attempt": attempt})
            try:
                resp = requests.request(
                    method,
                    url,
                    data=body_bytes,
                    headers=headers,
                    timeout=_DEFAULT_TIMEOUT,
                )
                latency = int((time.monotonic() - started) * 1000)
                if resp.status_code == 429 and attempt < _MAX_RETRIES:
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    time.sleep(min(retry_after, 30))
                    continue
                if resp.status_code >= 500 and attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))
                    continue

                body_text = resp.text or ""
                log.write(
                    {
                        "http_status": resp.status_code,
                        "latency_ms": latency,
                        "response_payload": body_text[:65000],
                    }
                )
                if resp.status_code >= 400:
                    log.write({"state": "failed", "error_message": f"HTTP {resp.status_code}"})
                    _circuit_record_failure(self.env, provider)
                    return {
                        "ok": False,
                        "http_status": resp.status_code,
                        "body": body_text,
                        "latency_ms": latency,
                        "log_id": log.id,
                    }
                log.write({"state": "ok"})
                _circuit_record_success(self.env, provider)
                try:
                    body = resp.json()
                except ValueError:
                    body = body_text
                return {
                    "ok": True,
                    "http_status": resp.status_code,
                    "body": body,
                    "latency_ms": latency,
                    "log_id": log.id,
                }
            except requests.Timeout as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))
                    continue
                latency = int((time.monotonic() - started) * 1000)
                log.write(
                    {
                        "state": "timeout",
                        "latency_ms": latency,
                        "error_message": f"timeout after {attempt} attempts: {e}",
                    }
                )
                _circuit_record_failure(self.env, provider)
                return {
                    "ok": False,
                    "http_status": 0,
                    "body": None,
                    "latency_ms": latency,
                    "log_id": log.id,
                }
            except requests.RequestException as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))
                    continue
                latency = int((time.monotonic() - started) * 1000)
                log.write(
                    {
                        "state": "failed",
                        "latency_ms": latency,
                        "error_message": f"transport: {e}",
                    }
                )
                _circuit_record_failure(self.env, provider)
                return {
                    "ok": False,
                    "http_status": 0,
                    "body": None,
                    "latency_ms": latency,
                    "log_id": log.id,
                }

        # Should not be reachable, but guard anyway.
        log.write(
            {
                "state": "failed",
                "error_message": f"all {_MAX_RETRIES} attempts exhausted: {last_exc}",
            }
        )
        _circuit_record_failure(self.env, provider)
        return {
            "ok": False,
            "http_status": 0,
            "body": None,
            "latency_ms": 0,
            "log_id": log.id,
        }
