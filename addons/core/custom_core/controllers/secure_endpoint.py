# -*- coding: utf-8 -*-
from __future__ import annotations

import functools
import hashlib
import hmac
import logging
import time
from ipaddress import ip_address, ip_network
from typing import Optional

from odoo.http import request

_logger = logging.getLogger(__name__)

# Process-local nonce cache: maps "<ts>:<sig>" -> seen_at_epoch.
# For multi-worker / horizontal deployments, the same key is also stored in Redis
# when a redis client is available via odoo conf (see _NonceStore).
_NONCE_CACHE: dict = {}
_NONCE_TTL_S = 600
_TS_DRIFT_MAX_S = 300

# Per-scope authentication modes. "hmac" is the platform standard and the default:
# a scope that never sets <scope>.auth_mode takes a byte-identical code path to the
# one that existed before api_key mode was added.
_AUTH_MODES = ("hmac", "api_key")


class _NonceStore:
    _redis_client = None
    _redis_probed = False

    @classmethod
    def _redis(cls):
        if cls._redis_probed:
            return cls._redis_client
        cls._redis_probed = True
        try:
            import redis  # type: ignore
            from odoo.tools import config as _odoo_config

            url = _odoo_config.get("custom_core_redis_url") or _odoo_config.get("redis_url")
            if url:
                cls._redis_client = redis.from_url(url, socket_timeout=0.25, socket_connect_timeout=0.25)
                cls._redis_client.ping()
        except Exception:
            cls._redis_client = None
        return cls._redis_client

    @classmethod
    def seen(cls, key: str) -> bool:
        # In-memory check first.
        now = time.time()
        for k, ts in list(_NONCE_CACHE.items()):
            if now - ts > _NONCE_TTL_S:
                _NONCE_CACHE.pop(k, None)
        if key in _NONCE_CACHE:
            return True
        client = cls._redis()
        if client is not None:
            try:
                redis_key = f"custom_core:nonce:{key}"
                # SET NX with TTL: returns True if not previously set.
                was_set = client.set(redis_key, "1", nx=True, ex=_NONCE_TTL_S)
                if not was_set:
                    return True
            except Exception as e:  # pragma: no cover
                _logger.warning("nonce redis check failed, falling back to memory: %s", e)
        _NONCE_CACHE[key] = now
        return False


def _get_param(scope: str, suffix: str, default: str = "") -> str:
    key = f"custom_core.secure_endpoint.{scope}.{suffix}"
    return request.env["ir.config_parameter"].sudo().get_param(key, default) or default


def _check_ip_whitelist(allowed_cidrs: str, remote: str) -> bool:
    if not allowed_cidrs:
        return True
    if not remote:
        return False
    try:
        addr = ip_address(remote)
    except ValueError:
        return False
    for chunk in allowed_cidrs.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            if "/" in chunk:
                if addr in ip_network(chunk, strict=False):
                    return True
            elif addr == ip_address(chunk):
                return True
        except ValueError:
            continue
    return False


def _verify_api_key(scope: str) -> Optional[str]:
    """Static-key auth. Returns an error code, or None when the caller is allowed.

    Deliberately weaker than HMAC: the key does not bind the body and there is no
    replay window. The compensating control is the CIDR allow-list, which is
    therefore MANDATORY in this mode -- an unset allow-list is a misconfiguration,
    not "allow all". This is the one place where api_key mode is stricter than hmac.

    Keys live in ``<scope>.api_keys`` (plural, comma-separated) so a rotation is
    "old,new" -> deploy -> "new". The HMAC ``.secret`` is never reused, so a scope
    cannot end up half-migrated between the two modes.
    """
    if not _get_param(scope, "allowed_cidrs", "").strip():
        return "NO_CIDR_CONFIGURED"
    configured = [k.strip() for k in _get_param(scope, "api_keys", "").split(",") if k.strip()]
    if not configured:
        return "NO_API_KEY_CONFIGURED"
    headers = request.httprequest.headers
    presented = (headers.get("X-API-Key") or "").strip()
    if not presented:
        auth = (headers.get("Authorization") or "").strip()
        if auth[:7].lower() == "bearer ":
            presented = auth[7:].strip()
    if not presented:
        return "MISSING_API_KEY"
    # compare_digest against every configured key, without short-circuiting, so the
    # loop's timing does not leak which key (or how many) matched.
    ok = False
    for key in configured:
        if hmac.compare_digest(key, presented):
            ok = True
    return None if ok else "BAD_API_KEY"


def _verify_hmac(scope: str, body: bytes, signature: str, timestamp: str) -> Optional[str]:
    if not signature or not timestamp:
        return "MISSING_AUTH_HEADERS"
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return "BAD_TIMESTAMP"
    if abs(time.time() - ts) > _TS_DRIFT_MAX_S:
        return "EXPIRED_TIMESTAMP"
    secret = _get_param(scope, "secret", "")
    if not secret:
        return "NO_SECRET_CONFIGURED"
    # Canonical form: ASCII timestamp || raw body bytes, HMAC-SHA256 hex.
    expected = hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return "BAD_SIGNATURE"
    nonce_key = f"{scope}:{ts}:{signature}"
    if _NonceStore.seen(nonce_key):
        return "REPLAY_NONCE"
    return None


def _log_attempt(scope: str, endpoint: str, body: bytes, status: int, error: Optional[str]) -> None:
    try:
        env = request.env(su=True)
        if "custom.adapter.call.log" in env:
            cfg = (
                env["custom.adapter.config"]
                .sudo()
                .search(
                    [("name", "=", f"secure_endpoint:{scope}")],
                    limit=1,
                )
            )
            if cfg:
                env["custom.adapter.call.log"].sudo().create(
                    {
                        "config_id": cfg.id,
                        "endpoint": endpoint,
                        "request_hash": hashlib.sha256(body or b"").hexdigest() if body else "",
                        "response_status": status,
                        "latency_ms": 0,
                        "error": (error or "")[:512] if error else False,
                        "ok": error is None,
                    }
                )
    except Exception as e:  # pragma: no cover
        _logger.debug("secure_endpoint log skipped: %s", e)


def secure_endpoint(scope_name: str):
    def _wrap(func):
        @functools.wraps(func)
        def _inner(*args, **kwargs):
            httpreq = request.httprequest
            remote = httpreq.environ.get("HTTP_X_FORWARDED_FOR") or httpreq.remote_addr or ""
            remote = remote.split(",")[0].strip()
            allowed = _get_param(scope_name, "allowed_cidrs", "")
            if not _check_ip_whitelist(allowed, remote):
                _logger.warning("secure_endpoint %s: IP %s not whitelisted", scope_name, remote)
                _log_attempt(scope_name, httpreq.path, b"", 403, "IP_NOT_ALLOWED")
                return request.make_json_response(
                    {"ok": False, "error_code": "IP_NOT_ALLOWED"},
                    status=403,
                )
            body = httpreq.get_data() or b""
            max_body = _get_param(scope_name, "max_body_bytes", "")
            if max_body.strip().isdigit() and len(body) > int(max_body.strip()):
                _logger.warning("secure_endpoint %s: body %s bytes over limit %s", scope_name, len(body), max_body)
                _log_attempt(scope_name, httpreq.path, b"", 413, "PAYLOAD_TOO_LARGE")
                return request.make_json_response(
                    {"ok": False, "error_code": "PAYLOAD_TOO_LARGE"},
                    status=413,
                )
            mode = (_get_param(scope_name, "auth_mode", "hmac") or "hmac").strip().lower()
            if mode not in _AUTH_MODES:
                err = "BAD_AUTH_MODE"
            elif mode == "api_key":
                err = _verify_api_key(scope_name)
            else:
                signature = httpreq.headers.get("X-Signature", "")
                timestamp = httpreq.headers.get("X-Timestamp", "")
                err = _verify_hmac(scope_name, body, signature, timestamp)
            if err:
                _logger.warning("secure_endpoint %s rejected: %s", scope_name, err)
                _log_attempt(scope_name, httpreq.path, body, 401, err)
                return request.make_json_response(
                    {"ok": False, "error_code": err},
                    status=401,
                )
            _log_attempt(scope_name, httpreq.path, body, 200, None)
            return func(*args, **kwargs)

        return _inner

    return _wrap


class SecureEndpointMixin:
    """Optional base class for controllers that want a `_secure(scope)` helper."""

    @staticmethod
    def secure(scope_name: str):
        return secure_endpoint(scope_name)
