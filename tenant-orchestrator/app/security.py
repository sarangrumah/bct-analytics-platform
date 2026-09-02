"""HMAC verification for every /v1/* route.

THE SCHEME IS NOT NEGOTIABLE HERE. custom_super_admin already signs its calls
with ``custom.security.sign_for``, which produces

    X-Custom-Signature: t=<unix_ts>,v1=<hex hmac_sha256(secret, f"{ts}.{body}")>

so this file verifies exactly that, byte for byte. Verified against the Odoo
side before a line of it was written: ``custom_core/models/custom_security.py``
builds ``msg = str(ts).encode() + b"." + body`` and hexdigests it, and so does
``_expected`` below. Inventing a nicer scheme here would mean editing an
imported module to match, and every one of its callers with it.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("orchestrator.security")

#: Unsigned, because they carry nothing and are needed to diagnose the service
#: when signing itself is what is broken.
EXEMPT = frozenset({"/health", "/healthz", "/metrics"})


def _deny(detail: str) -> JSONResponse:
    # One shape for every rejection. Distinguishing "bad timestamp" from "bad
    # signature" in the RESPONSE would let a caller tune an attack against the
    # window; the distinction is kept in the log instead, where the operator
    # can see it and the caller cannot.
    return JSONResponse({"error": "unauthorized", "detail": detail}, status_code=401)


class HMACMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, secret: str, window_seconds: int) -> None:
        super().__init__(app)
        self._secret = secret.encode()
        self._window = window_seconds

    async def dispatch(self, request, call_next):
        path = request.url.path
        if path in EXEMPT or not path.startswith("/v1/"):
            return await call_next(request)

        header = request.headers.get("X-Custom-Signature", "")
        if not header:
            logger.warning("hmac.missing path=%s", path)
            return _deny("Request is not signed.")

        try:
            parts = dict(p.split("=", 1) for p in header.split(","))
            ts_raw = parts["t"]
            given = parts["v1"]
            ts = int(ts_raw)
        except (KeyError, ValueError):
            logger.warning("hmac.malformed path=%s", path)
            return _deny("Request is not signed.")

        skew = abs(time.time() - ts)
        if skew > self._window:
            logger.warning("hmac.stale path=%s skew=%ss", path, int(skew))
            return _deny("Request is not signed.")

        body = await request.body()
        expected = hmac.new(
            self._secret, ts_raw.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, given):
            logger.warning("hmac.mismatch path=%s", path)
            return _deny("Request is not signed.")

        # Starlette consumes the body stream when it is read, so a handler that
        # reads it again would see nothing. Put it back.
        async def _receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = _receive  # noqa: SLF001 - the documented way to do this

        # Who asked. Set by Odoo from its own session, and recorded in the
        # append-only action log so "who suspended this tenant" has an answer.
        request.state.actor = request.headers.get("X-Custom-Actor", "unknown")
        return await call_next(request)
