# -*- coding: utf-8 -*-
"""Generic HTTP JSON provider adapter (HMAC-SHA256 signed).

Transport is a single signed POST per call -- there is deliberately NO retry
loop, so a non-idempotent ``pay()`` is never re-sent and cannot double-sell
(D1). Resilience across a flaky provider is handled at a higher layer by
provider failover and the stale-transaction reaper.

Credentials and base URL are resolved from ``provider.adapter_config_id``
(a ``custom.adapter.config`` row) when set -- giving per-tenant credential
management and reusing the platform adapter registry's audit log
(``custom.adapter.call.log``). It falls back to the provider's own
``endpoint_url`` / ``credential_ref`` fields when no config row is linked.

External teams typically replace this with a provider-specific subclass
(e.g. Digiflazz) registered under its own ``ppob_*`` name.
"""

import hashlib
import hmac
import json
import logging
import time

import requests

from .ppob_provider_adapter_base import (
    AdapterResult,
    PPOBProviderAdapter,
    register_adapter,
)

_logger = logging.getLogger(__name__)


@register_adapter("ppob_http_json")
class HttpJsonProviderAdapter(PPOBProviderAdapter):
    DEFAULT_TIMEOUT = 15

    # ------------------------------------------------------------------
    # Config resolution (prefer custom.adapter.config, fall back to provider)
    # ------------------------------------------------------------------

    def _config(self):
        return self.provider.adapter_config_id or self.provider.env["custom.adapter.config"].browse()

    def _base_url(self):
        cfg = self._config()
        return (cfg.base_url if cfg else self.provider.endpoint_url) or ""

    def _timeout(self):
        cfg = self._config()
        return (cfg.timeout_s if cfg else self.DEFAULT_TIMEOUT) or self.DEFAULT_TIMEOUT

    def _get_credential(self):
        cfg = self._config()
        key = cfg.credential_ref if cfg else self.provider.credential_ref
        if not key:
            return ""
        return self.provider.env["ir.config_parameter"].sudo().get_param(key, "") or ""

    # ------------------------------------------------------------------
    # Transport (single signed POST, no retry -> pay-safe)
    # ------------------------------------------------------------------

    def _log_call(self, path, body, status_code, latency_ms, error, ok):
        cfg = self._config()
        if not cfg:
            return
        try:
            self.provider.env["custom.adapter.call.log"].sudo().create(
                {
                    "config_id": cfg.id,
                    "endpoint": path,
                    "request_hash": hashlib.sha256(body or b"").hexdigest() if body else "",
                    "response_status": status_code,
                    "latency_ms": latency_ms,
                    "error": (error or "")[:512] if error else False,
                    "ok": ok,
                }
            )
        except Exception as e:  # pragma: no cover - never block the business call
            _logger.error("ppob http adapter call log write failed: %s", e)

    def _post(self, path, payload):
        url = self._base_url().rstrip("/") + "/" + path.lstrip("/")
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        secret = self._get_credential().encode("utf-8")
        ts = str(int(time.time()))
        signature = hmac.new(secret, ts.encode("utf-8") + body, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Signature": signature,
            "X-Timestamp": ts,
        }
        t0 = time.time()
        try:
            resp = requests.post(url, data=body, headers=headers, timeout=self._timeout())
            latency_ms = int((time.time() - t0) * 1000)
            data = resp.json() if resp.content else {}
            ok = 200 <= resp.status_code < 300
            self._log_call(path, body, resp.status_code, latency_ms, None if ok else f"HTTP{resp.status_code}", ok)
            return resp.status_code, data if isinstance(data, dict) else {"_raw": data}
        except requests.RequestException as exc:
            latency_ms = int((time.time() - t0) * 1000)
            _logger.warning("HTTP provider %s call failed: %s", self.provider.code, exc)
            self._log_call(path, body, 0, latency_ms, str(exc), False)
            return 0, {"error": str(exc)}

    def _result_from(self, status_code, data):
        data = data or {}
        ok = 200 <= status_code < 300 and data.get("ok", True)
        return AdapterResult(
            ok=ok,
            provider_ref=data.get("provider_ref") or data.get("ref"),
            serial_token=data.get("serial_token") or data.get("token"),
            amount=data.get("amount"),
            raw=data,
            error_code=None if ok else (data.get("error_code") or f"HTTP{status_code}"),
            error_message=None if ok else data.get("error") or data.get("message"),
        )

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    def inquiry(self, transaction):
        status, data = self._post(
            "inquiry",
            {
                "sku": transaction.provider_sku,
                "msisdn": transaction.msisdn,
                "ref": transaction.name,
            },
        )
        return self._result_from(status, data)

    def pay(self, transaction):
        status, data = self._post(
            "pay",
            {
                "sku": transaction.provider_sku,
                "msisdn": transaction.msisdn,
                "ref": transaction.name,
                "amount": transaction.cost_price,
                "inquiry_ref": transaction.inquiry_reference,
                "idempotency_key": transaction.idempotency_key,
            },
        )
        return self._result_from(status, data)

    def status(self, provider_ref):
        status, data = self._post("status", {"ref": provider_ref})
        return self._result_from(status, data)

    def topup(self, amount):
        status, data = self._post("topup", {"amount": amount})
        return self._result_from(status, data)
