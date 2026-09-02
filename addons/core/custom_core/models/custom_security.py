# -*- coding: utf-8 -*-
"""HMAC signing helpers used by custom_ai_bridge & Coretax adapters."""

from __future__ import annotations

import hashlib
import hmac
import os
import time

from odoo import api, models


class CustomSecurity(models.AbstractModel):
    _name = "custom.security"
    _description = "HMAC signing helpers"

    @api.model
    def _gateway_secret(self) -> str:
        secret = os.environ.get("GATEWAY_SHARED_SECRET", "")
        if not secret or "changeme" in secret:
            raise RuntimeError("GATEWAY_SHARED_SECRET not properly set in env")
        return secret

    @api.model
    def _orchestrator_secret(self) -> str:
        secret = os.environ.get("ORCHESTRATOR_SHARED_SECRET", "")
        if not secret or "changeme" in secret:
            raise RuntimeError("ORCHESTRATOR_SHARED_SECRET not properly set in env")
        return secret

    @api.model
    def sign_for(self, secret_key: str, body: bytes) -> tuple[str, int]:
        """Generic signer. ``secret_key`` is the env var name."""
        secret = os.environ.get(secret_key, "")
        if not secret or "changeme" in secret:
            raise RuntimeError(f"{secret_key} not properly set in env")
        ts = int(time.time())
        msg = str(ts).encode() + b"." + body
        sig = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
        return f"t={ts},v1={sig}", ts

    @api.model
    def sign_payload(self, body: bytes) -> tuple[str, int]:
        """Return (header_value, timestamp) for X-Custom-Signature.

        Header format: ``t=<unix_ts>,v1=<hex_hmac>``
        """
        ts = int(time.time())
        secret = self._gateway_secret()
        msg = str(ts).encode() + b"." + body
        sig = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
        return f"t={ts},v1={sig}", ts

    @api.model
    def verify_signature(self, header: str, body: bytes, max_skew: int = 300) -> bool:
        """Verify an X-Custom-Signature header. Returns True on match within skew window."""
        try:
            parts = dict(p.split("=", 1) for p in header.split(","))
            ts = int(parts["t"])
            given = parts["v1"]
        except (KeyError, ValueError):
            return False
        if abs(time.time() - ts) > max_skew:
            return False
        secret = self._gateway_secret()
        msg = str(ts).encode() + b"." + body
        expected = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, given)
