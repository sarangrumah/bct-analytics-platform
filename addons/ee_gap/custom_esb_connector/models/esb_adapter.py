# -*- coding: utf-8 -*-
"""Adapters for the three ESB API hosts.

ESB does not serve everything from one base URL. ``/auth``, ``/inventory/*``,
``/purchase/*`` and ``/report/*`` live on the Core host; ``/corev1/*`` and
``/extv1/*`` on a second host; the OMS ``/external/general/*`` feeds on a third.
Each gets its own ``custom.adapter.config`` row, all three sharing this class.

Everything below subclasses ``BaseAdapter`` from ``custom_adapter_framework``, so
retry/backoff, the circuit breaker and ``custom.adapter.call.log`` come for free.
Three ESB-specific behaviours are layered on top:

1. **Envelope unwrapping.** Every ESB response is
   ``{status, code, message, result, errors}`` and a business failure arrives as
   ``HTTP 200`` with ``status: "fail"``. ``_handle_response`` rewrites those to a
   4xx status code so ``BaseAdapter`` treats them as permanent — retrying a
   validation error would only trip the breaker.
2. **Session-managed bearer token.** ``_get_secret`` returns the live access
   token from ``custom.esb.session`` instead of a static config parameter.
3. **Query-string GETs.** ``BaseAdapter.call`` only sends a body; every ESB read
   endpoint is query-param driven, so ``get()``/``iter_rows()`` build the URL.

See ``docs/integrations/esb-core-api.md``.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from odoo.addons.custom_adapter_framework.models.adapter_base import AdapterResponse, BaseAdapter
from odoo.addons.custom_adapter_framework.models.adapter_registry import register_adapter

_logger = logging.getLogger(__name__)

ESB_CORE = "esb_core"
ESB_COREV1 = "esb_corev1"
ESB_OMS = "esb_oms"

OK_CODE = "EC03100000"
#: Unauthorized / Invalid Token. Worth one transparent re-login before giving up.
AUTH_ERROR_CODES = frozenset({"EC03100001"})
#: Invalid username or password — a re-login would fail identically, so never retry.
CREDENTIAL_ERROR_CODES = frozenset({"EC03100032"})

#: Synthetic status codes used when the envelope disagrees with the HTTP status.
#: Both are 4xx so BaseAdapter.call() returns immediately instead of retrying.
ENVELOPE_AUTH_STATUS = 401
ENVELOPE_FAIL_STATUS = 422

#: ESB caps the stock-movement report at 100 rows per page.
MAX_PAGE_LIMIT = 100
#: Backstop so a mis-paginating endpoint cannot spin forever.
MAX_PAGES = 500


class EsbApiError(Exception):
    """An ESB call failed. Carries the envelope code when there was one."""

    def __init__(self, message, code=None, status_code=0):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@register_adapter(ESB_CORE)
class EsbCoreAdapter(BaseAdapter):
    """ESB Core host — auth, inventory, purchasing, master data, reports."""

    def __init__(self, config):
        super().__init__(config)
        # Set while performing /auth/login or /auth/refresh, which must not try to
        # resolve an access token (that is what they exist to produce).
        self._no_auth = False
        # Guards the transparent re-login retry against recursing.
        self._auth_retry_done = False

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _get_secret(self) -> str:
        if self._no_auth:
            return ""
        session = self.env["custom.esb.session"].sudo()._for_config(self.config)
        if not session:
            return ""
        return session._ensure_token() or ""

    def login(self, username: str, password: str) -> AdapterResponse:
        """POST /auth/login. Called only by ``custom.esb.session``."""
        self._no_auth = True
        try:
            return self.call("auth/login", payload={"username": username, "password": password}, method="POST")
        finally:
            self._no_auth = False

    def refresh(self, refresh_token: str) -> AdapterResponse:
        """GET /auth/refresh, authenticated with the *refresh* token."""
        self._no_auth = True
        try:
            return self.call(
                "auth/refresh",
                payload=None,
                method="GET",
                extra_headers={"Authorization": f"Bearer {refresh_token}"},
            )
        finally:
            self._no_auth = False

    # ------------------------------------------------------------------
    # Call plumbing
    # ------------------------------------------------------------------

    def call(self, endpoint, payload=None, timeout=None, method="POST", extra_headers=None):
        resp = super().call(endpoint, payload=payload, timeout=timeout, method=method, extra_headers=extra_headers)
        # One transparent re-login: the token may have been evicted by another
        # login on the same ESB credentials (ESB allows only one live session).
        if not resp.ok and not self._no_auth and not self._auth_retry_done and self._is_auth_error(resp):
            session = self.env["custom.esb.session"].sudo()._for_config(self.config)
            if session:
                _logger.info("ESB %s: token rejected, forcing re-login and retrying once", self.config.name)
                session._invalidate_token()
                self._auth_retry_done = True
                try:
                    resp = super().call(
                        endpoint, payload=payload, timeout=timeout, method=method, extra_headers=extra_headers
                    )
                finally:
                    self._auth_retry_done = False
        return resp

    def _handle_response(self, resp, latency_ms: int) -> AdapterResponse:
        result = super()._handle_response(resp, latency_ms)
        data = result.data if isinstance(result.data, dict) else None
        if data is None or "status" not in data:
            # Not an ESB envelope (gateway error page, empty body, ...) — leave as is.
            return result
        code = data.get("code")
        if data.get("status") == "ok" and code in (None, OK_CODE):
            return result
        # Envelope-level failure. Rewrite to 4xx so BaseAdapter treats it as
        # permanent: neither retried nor counted against the circuit breaker.
        result.ok = False
        result.error = self._envelope_error(data)
        result.headers = dict(result.headers or {})
        result.headers["X-Esb-Http-Status"] = str(result.status_code)
        result.status_code = ENVELOPE_AUTH_STATUS if code in AUTH_ERROR_CODES else ENVELOPE_FAIL_STATUS
        return result

    @staticmethod
    def _envelope_error(data: dict) -> str:
        parts = [str(data.get("code") or "?"), str(data.get("message") or "")]
        for err in data.get("errors") or []:
            if isinstance(err, dict):
                parts.append(f"{err.get('attribute') or ''}:{err.get('message') or err.get('code') or ''}".strip(":"))
        return " | ".join(p for p in parts if p)[:500]

    @staticmethod
    def _is_auth_error(resp: AdapterResponse) -> bool:
        if resp.status_code == ENVELOPE_AUTH_STATUS:
            return True
        data = resp.data if isinstance(resp.data, dict) else {}
        return data.get("code") in AUTH_ERROR_CODES

    def health_check(self) -> AdapterResponse:
        """ESB has no /health. A branch list is the cheapest authenticated read."""
        return self.get("branch")

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def get(self, path: str, params: dict | None = None, timeout: int | None = None) -> AdapterResponse:
        """GET with a query string. Empty/None params are dropped, not sent blank."""
        if params:
            clean = {k: v for k, v in params.items() if v not in (None, "", False)}
            if clean:
                sep = "&" if "?" in path else "?"
                path = f"{path}{sep}{urlencode(clean)}"
        return self.call(path, payload=None, method="GET", timeout=timeout)

    def get_rows(self, path: str, params: dict | None = None) -> list:
        """GET one page and return its rows, raising ``EsbApiError`` on failure.

        Handles both envelope shapes: ``result`` as a bare list (``/branch``,
        ``/location``, ``/units``) and ``result.data`` as a paged list.
        """
        resp = self.get(path, params)
        if not resp.ok:
            raise EsbApiError(resp.error or "ESB call failed", status_code=resp.status_code)
        return self._rows(resp)

    @staticmethod
    def _rows(resp: AdapterResponse) -> list:
        result = (resp.data or {}).get("result")
        if result is None:
            return []
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            rows = result.get("data")
            if isinstance(rows, list):
                return rows
            if rows is None and "count" in result:
                # Documented "no data" shape: {"count": 0, "data": null}.
                return []
            # Single-object result (e.g. View endpoints).
            return [result]
        return []

    def iter_rows(self, path: str, params: dict | None = None, limit: int = MAX_PAGE_LIMIT):
        """Yield every row across pages.

        Stops on a short page, an empty page, or when ``result.count`` has been
        reached — ESB's ``next`` URL is unreliable (it is emitted even when the
        result set is empty, and double-appends the query string).
        """
        limit = max(1, min(limit, MAX_PAGE_LIMIT))
        base = dict(params or {})
        seen = 0
        for page in range(1, MAX_PAGES + 1):
            resp = self.get(path, {**base, "page": page, "limit": limit})
            if not resp.ok:
                raise EsbApiError(resp.error or "ESB call failed", status_code=resp.status_code)
            rows = self._rows(resp)
            if not rows:
                return
            for row in rows:
                yield row
            seen += len(rows)
            result = (resp.data or {}).get("result")
            total = result.get("count") if isinstance(result, dict) else None
            if len(rows) < limit:
                return
            # `count` is documented as the total, but some endpoints return the
            # per-page count instead. Only trust it to stop when it exceeds the
            # page size, otherwise fall back to the short-page rule above.
            if isinstance(total, int) and total > limit and seen >= total:
                return
        _logger.warning("ESB %s: pagination hit MAX_PAGES on %s", self.config.name, path)


@register_adapter(ESB_COREV1)
class EsbCoreV1Adapter(EsbCoreAdapter):
    """``corev1``/``extv1`` host — richer product master, GR inquiry, sales feeds.

    Same protocol and the same token; only the base URL differs.
    """


@register_adapter(ESB_OMS)
class EsbOmsAdapter(EsbCoreAdapter):
    """ESB OMS ``external/general/*`` host — POS sales and material usage.

    These endpoints are POST-with-body filters and paginate via ``X-Pagination-*``
    response headers rather than the envelope, so pagination is handled here.
    """

    def post_rows(self, path: str, body: dict, page: int = 1) -> AdapterResponse:
        sep = "&" if "?" in path else "?"
        return self.call(f"{path}{sep}{urlencode({'page': page})}", payload=body, method="POST")

    def iter_post_rows(self, path: str, body: dict):
        for page in range(1, MAX_PAGES + 1):
            resp = self.post_rows(path, body, page=page)
            if not resp.ok:
                raise EsbApiError(resp.error or "ESB call failed", status_code=resp.status_code)
            rows = self._rows(resp)
            if not rows:
                return
            for row in rows:
                yield row
            headers = resp.headers or {}
            total_pages = headers.get("X-Pagination-Page-Count") or headers.get("x-pagination-page-count")
            try:
                if total_pages is not None and page >= int(total_pages):
                    return
            except (TypeError, ValueError):
                pass
