# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

from .adapter_registry import register_adapter, get_adapter_class, list_adapter_classes  # noqa: F401

_logger = logging.getLogger(__name__)


@dataclass
class AdapterResponse:
    ok: bool
    status_code: int = 0
    data: Optional[dict] = None
    error: Optional[str] = None
    latency_ms: int = 0
    raw_text: Optional[str] = None
    headers: dict = field(default_factory=dict)


class CircuitBreakerOpenError(RuntimeError):
    pass


class BaseAdapter:
    DEFAULT_TIMEOUT_S = 15
    DEFAULT_RETRY_COUNT = 3
    DEFAULT_CB_THRESHOLD = 5
    DEFAULT_CB_COOLDOWN_S = 60
    BACKOFF_BASE_S = 0.25
    BACKOFF_CAP_S = 8.0

    def __init__(self, config):
        self.config = config
        self.env = config.env if config is not None else None

    # --- protocol ---

    def health_check(self) -> AdapterResponse:
        return self.call("health", payload=None, method="GET")

    def call(
        self,
        endpoint: str,
        payload: Any = None,
        timeout: Optional[int] = None,
        method: str = "POST",
        extra_headers: Optional[dict] = None,
    ) -> AdapterResponse:
        self._cb_precheck()
        timeout = timeout or (self.config.timeout_s if self.config else self.DEFAULT_TIMEOUT_S)
        retries = self.config.retry_count if self.config else self.DEFAULT_RETRY_COUNT
        url = self._build_url(endpoint)
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        last_exc: Optional[Exception] = None
        for attempt in range(max(1, retries)):
            t0 = time.time()
            try:
                headers = self._build_headers(body, extra_headers)
                resp = requests.request(method, url, data=body if body else None, headers=headers, timeout=timeout)
                latency_ms = int((time.time() - t0) * 1000)
                result = self._handle_response(resp, latency_ms)
                self._log_call(endpoint, body, result)
                if result.ok or 400 <= result.status_code < 500:
                    # 4xx is a permanent failure: do not retry, do not trip breaker.
                    if result.ok:
                        self._cb_record_success()
                    return result
                # 5xx / network-ish: retry with backoff
                self._cb_record_failure()
            except requests.RequestException as exc:
                latency_ms = int((time.time() - t0) * 1000)
                last_exc = exc
                _logger.warning(
                    "Adapter %s call %s failed (attempt %s): %s",
                    getattr(self.config, "name", "?"),
                    endpoint,
                    attempt + 1,
                    exc,
                )
                self._cb_record_failure()
                fail = AdapterResponse(ok=False, status_code=0, error=str(exc), latency_ms=latency_ms)
                self._log_call(endpoint, body, fail)
            # exponential backoff with cap, only if more attempts remain
            if attempt + 1 < retries:
                delay = min(self.BACKOFF_CAP_S, self.BACKOFF_BASE_S * (2**attempt))
                time.sleep(delay)
        return AdapterResponse(ok=False, status_code=0, error=str(last_exc) if last_exc else "exhausted_retries")

    # --- signing ---

    def _sign_request(self, body: bytes, ts: str) -> str:
        secret = (self._get_secret() or "").encode("utf-8")
        # Canonical form: timestamp bytes || raw body bytes; HMAC-SHA256 hex.
        return hmac.new(secret, ts.encode("utf-8") + body, hashlib.sha256).hexdigest()

    def _get_secret(self) -> str:
        if not self.config or not self.config.credential_ref:
            return ""
        return self.env["ir.config_parameter"].sudo().get_param(self.config.credential_ref, "") or ""

    def _build_headers(self, body: bytes, extra: Optional[dict]) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        auth = self.config.auth_method if self.config else "none"
        if auth == "hmac":
            ts = str(int(time.time()))
            headers["X-Timestamp"] = ts
            headers["X-Signature"] = self._sign_request(body, ts)
        elif auth == "bearer":
            headers["Authorization"] = f"Bearer {self._get_secret()}"
        elif auth == "basic":
            import base64

            token = base64.b64encode(self._get_secret().encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        if extra:
            headers.update(extra)
        return headers

    def _build_url(self, endpoint: str) -> str:
        base = (self.config.base_url if self.config else "").rstrip("/")
        return f"{base}/{endpoint.lstrip('/')}"

    # --- response handling ---

    def _handle_response(self, resp, latency_ms: int) -> AdapterResponse:
        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = None
        ok = 200 <= resp.status_code < 300
        return AdapterResponse(
            ok=ok,
            status_code=resp.status_code,
            data=data if isinstance(data, dict) else ({"_raw": data} if data is not None else None),
            error=None if ok else (isinstance(data, dict) and data.get("error")) or f"HTTP{resp.status_code}",
            latency_ms=latency_ms,
            raw_text=resp.text if not ok else None,
            headers=dict(resp.headers or {}),
        )

    # --- circuit breaker ---
    # States: closed (normal) -> open (block calls) after threshold consecutive failures;
    # after cooldown elapses we move to half-open (allow one probe). A success in half-open
    # returns to closed; a failure returns to open and restarts the cooldown.

    def _cb_precheck(self) -> None:
        if not self.config:
            return
        if self.config.status == "circuit_open":
            cooldown = self.config.circuit_breaker_cooldown_s or self.DEFAULT_CB_COOLDOWN_S
            opened_at = self.config.circuit_opened_at
            if opened_at and (time.time() - opened_at.timestamp()) >= cooldown:
                # Allow a probe: move to half-open by flipping status to active in-memory only.
                # The probe outcome will commit the transition via _cb_record_*.
                self.config.sudo().write({"status": "active"})
            else:
                raise CircuitBreakerOpenError(f"adapter {self.config.name} circuit open; cooldown not elapsed")

    def _cb_record_success(self) -> None:
        if not self.config:
            return
        if self.config.consecutive_failures or self.config.status != "active":
            self.config.sudo().write(
                {
                    "consecutive_failures": 0,
                    "status": "active",
                    "circuit_opened_at": False,
                }
            )

    def _cb_record_failure(self) -> None:
        if not self.config:
            return
        threshold = self.config.circuit_breaker_threshold or self.DEFAULT_CB_THRESHOLD
        new_count = (self.config.consecutive_failures or 0) + 1
        vals = {"consecutive_failures": new_count}
        if new_count >= threshold:
            vals["status"] = "circuit_open"
            vals["circuit_opened_at"] = self._now_dt()
        self.config.sudo().write(vals)

    @staticmethod
    def _now_dt():
        from odoo import fields as _fields

        return _fields.Datetime.now()

    # --- audit log hook ---

    def _log_call(self, endpoint: str, body: bytes, result: AdapterResponse) -> None:
        if not self.config or not self.env:
            return
        try:
            req_hash = hashlib.sha256(body or b"").hexdigest() if body else ""
            self.env["custom.adapter.call.log"].sudo().create(
                {
                    "config_id": self.config.id,
                    "endpoint": endpoint,
                    "request_hash": req_hash,
                    "response_status": result.status_code,
                    "latency_ms": result.latency_ms,
                    "error": (result.error or "")[:512] if result.error else False,
                    "ok": result.ok,
                }
            )
        except Exception as e:  # pragma: no cover - never block business call
            _logger.error("adapter call log write failed: %s", e)
