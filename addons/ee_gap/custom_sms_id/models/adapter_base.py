# -*- coding: utf-8 -*-
"""Adapter base for SMS providers.

Provides:
  * A dispatcher mapping a ``custom.sms.account`` to its concrete
    adapter (Zenziva / Twilio).
  * A shared HTTP helper (``_post``) with retries, exponential
    backoff, and ``Retry-After`` handling for 429s.
  * An in-memory per-account circuit breaker (10 failures within
    60 seconds → block for 5 minutes).

Pattern aligned with ``custom_coretax_pajakku.coretax_adapter_pajakku``.
Concrete subclasses (``custom.sms.adapter.zenziva``,
``custom.sms.adapter.twilio``) override ``send`` and ``test_connection``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


# ============================================================
# Module-level circuit breaker state (per-process, per-account)
# ============================================================
# Shape: {account_id: {"failures": [ts, ts, ...], "open_until": float}}
_CB_STATE: dict[int, dict[str, Any]] = {}

# 10 failures within this window trip the breaker
_CB_WINDOW_SECONDS = 60
_CB_THRESHOLD = 10
# Breaker stays open for this long once tripped
_CB_OPEN_SECONDS = 300  # 5 minutes

# Retry policy for transient HTTP errors
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds; doubles each attempt → 1s, 2s, 4s


def _now() -> float:
    return time.monotonic()


def _circuit_open(account_id: int) -> bool:
    st = _CB_STATE.get(account_id)
    if not st:
        return False
    return _now() < st.get("open_until", 0)


def _circuit_record_success(account_id: int) -> None:
    """Successful call: clear failure history for this account."""
    _CB_STATE.pop(account_id, None)


def _circuit_record_failure(account_id: int) -> bool:
    """Record a failure. Returns True if the breaker tripped open."""
    now = _now()
    st = _CB_STATE.setdefault(account_id, {"failures": [], "open_until": 0.0})
    # prune failures outside the rolling window
    cutoff = now - _CB_WINDOW_SECONDS
    st["failures"] = [t for t in st["failures"] if t >= cutoff]
    st["failures"].append(now)
    if len(st["failures"]) >= _CB_THRESHOLD:
        st["open_until"] = now + _CB_OPEN_SECONDS
        return True
    return False


class CustomSmsAdapterBase(models.AbstractModel):
    _name = "custom.sms.adapter.base"
    _description = "SMS Adapter Base"

    # -------- Dispatcher --------

    @api.model
    def _get_for_account(self, account):
        """Resolve the concrete adapter for a given ``custom.sms.account``."""
        provider = getattr(account, "provider", None)
        if provider == "zenziva":
            return self.env["custom.sms.adapter.zenziva"]
        if provider == "twilio":
            return self.env["custom.sms.adapter.twilio"]
        raise UserError(_("Unknown SMS provider: %s") % provider)

    # -------- Public API (overridden by subclasses) --------

    @api.model
    def send(self, account, to_phone: str, body: str, *, purpose: str | None = None) -> dict:
        """Send a single SMS via the provider.

        Subclasses must override. Returns a dict with at least::

            {"ok": bool, "provider_message_id": str | None, "message": str}
        """
        raise NotImplementedError("custom.sms.adapter.base.send must be overridden by a provider subclass")

    @api.model
    def test_connection(self, account) -> dict:
        """Lightweight probe: validate credentials / endpoint without sending."""
        return {"ok": False, "message": _("test_connection not implemented for this provider")}

    @api.model
    def poll_status(self, account, provider_message_id: str) -> dict:
        """Optional provider-status lookup. Default no-op."""
        return {"ok": False, "status": None, "message": _("poll_status not supported")}

    # -------- Shared HTTP helper --------

    @api.model
    def _check_circuit(self, account) -> None:
        """Raise UserError if the breaker for ``account`` is open."""
        if _circuit_open(account.id):
            raise UserError(
                _(
                    "SMS circuit breaker is OPEN for account '%s'. Will auto-reset "
                    "in up to 5 minutes, or fix the underlying error and retry."
                )
                % account.display_name
            )

    @api.model
    def _post(
        self, url: str, data: dict | None = None, *, auth: tuple | None = None, timeout: int = 30, account=None
    ) -> requests.Response:
        """POST with retry x3 exponential backoff + circuit-breaker awareness.

        Honours ``Retry-After`` on HTTP 429. Logs request latency.
        Returns the raw ``requests.Response`` on success.
        Raises ``RuntimeError`` after all retries are exhausted.
        """
        account_id = account.id if account else 0
        if account and _circuit_open(account_id):
            raise UserError(_("SMS circuit breaker OPEN for account '%s'. Refusing to POST.") % account.display_name)

        attempt = 0
        last_exc: Exception | None = None
        while attempt < _MAX_RETRIES:
            attempt += 1
            start = time.monotonic()
            try:
                resp = requests.post(url, data=data, auth=auth, timeout=timeout)
                latency_ms = (time.monotonic() - start) * 1000.0
                _logger.info(
                    "[custom_sms_id] POST %s attempt=%s status=%s latency_ms=%.1f",
                    url,
                    attempt,
                    resp.status_code,
                    latency_ms,
                )

                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After", "5")
                    try:
                        wait_s = int(retry_after)
                    except (TypeError, ValueError):
                        wait_s = 5
                    if attempt < _MAX_RETRIES:
                        time.sleep(min(wait_s, 30))
                        continue
                    # exhausted: record fail + raise
                    if account:
                        if _circuit_record_failure(account_id):
                            _logger.warning(
                                "[custom_sms_id] circuit OPENED for account %s after 429",
                                account_id,
                            )
                    raise RuntimeError(f"HTTP 429 after {_MAX_RETRIES} attempts (Retry-After={retry_after})")

                if resp.status_code >= 500 and attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))
                    continue

                if resp.status_code >= 400:
                    if account:
                        if _circuit_record_failure(account_id):
                            _logger.warning(
                                "[custom_sms_id] circuit OPENED for account %s after HTTP %s",
                                account_id,
                                resp.status_code,
                            )
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

                # success
                if account:
                    _circuit_record_success(account_id)
                return resp

            except requests.RequestException as e:
                last_exc = e
                _logger.warning(
                    "[custom_sms_id] POST %s attempt=%s transport-error: %s",
                    url,
                    attempt,
                    e,
                )
                if attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))
                    continue
                if account:
                    if _circuit_record_failure(account_id):
                        _logger.warning(
                            "[custom_sms_id] circuit OPENED for account %s after transport error",
                            account_id,
                        )
                raise RuntimeError(f"Transport error after {_MAX_RETRIES} attempts: {e}") from e

        raise RuntimeError(f"All {_MAX_RETRIES} attempts failed; last: {last_exc}")
