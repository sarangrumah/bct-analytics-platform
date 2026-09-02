"""The login gateway — Odoo credential in, contract 02 session out.

Responsibilities, and nothing beyond them:

* authenticate against Odoo over JSON-RPC (``common.authenticate``);
* read the user's company and Operating Unit entitlement;
* mint the RS256 access token of frozen contract 02;
* publish the **public** halves of two signing keys at ``/.well-known/jwks.json`` (finding T-4);
* hand out and rotate an opaque refresh token in an httpOnly cookie.

It never queries the warehouse, never sees a metric, and never holds a database credential for
anything but Odoo's JSON-RPC. The signing keys live here and nowhere else: verifiers hold public
material only, which is the property that makes a key rotation a config change instead of a
redeployment of every consumer.
"""

from __future__ import annotations

import datetime as dt
import logging
import secrets
import threading
import time

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from pydantic import BaseModel, Field

from .config import settings_from_env
from .keys import load_key_ring
from .odoo import AuthenticationFailed, OdooClient, OdooError, read_session_claims
from .ratelimit import RateLimiter
from .registry import Registry
from .tokens import mint_access_token, mint_refresh_token

_logger = logging.getLogger("login_gateway")

AUTH_TOTAL = Counter(
    "bct_gateway_auth_total", "Authentication attempts by outcome.", ["result"]
)
TOKENS_ISSUED = Counter(
    "bct_gateway_token_issued_total", "Access tokens issued.", ["tenant"]
)
JWKS_KEYS = Gauge(
    "bct_gateway_jwks_keys",
    "Number of keys published in JWKS. Two is the floor: a single-key JWKS cannot be rotated "
    "without a flag-day outage (security finding T-4).",
)


class LoginRequest(BaseModel):
    db: str = Field(min_length=1, max_length=63)
    login: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class RefreshStore:
    """Server-side refresh state, so that logout actually revokes.

    In-process for the same reason the rate limiter is: one replica, one port. Replicating the
    gateway means moving this to shared storage, and that is written down here rather than
    discovered when the second replica starts handing out 401s.
    """

    def __init__(self) -> None:
        self._tokens = {}
        self._lock = threading.Lock()

    def issue(self, tenant: str, uid: int, ttl: int) -> str:
        token = mint_refresh_token()
        with self._lock:
            self._tokens[token] = {
                "tenant": tenant,
                "uid": uid,
                "expires": time.time() + ttl,
            }
        return token

    def consume(self, token: str):
        """Single-use: a refresh token is invalidated as it is redeemed.

        Rotation on every refresh means a stolen-and-replayed token collides with the legitimate
        client's next refresh, so the theft surfaces as a failed session rather than as a quiet
        parallel session that lasts forever.
        """
        with self._lock:
            record = self._tokens.pop(token, None)
        if record is None or record["expires"] < time.time():
            return None
        return record

    def revoke(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)

    def purge(self) -> None:
        now = time.time()
        with self._lock:
            for token in [t for t, r in self._tokens.items() if r["expires"] < now]:
                del self._tokens[token]


def create_app(settings=None) -> FastAPI:
    settings = settings or settings_from_env()
    ring = load_key_ring(settings)
    JWKS_KEYS.set(len(ring.keys))

    odoo = OdooClient(settings.odoo_url)
    limiter = RateLimiter(
        settings.rate_limit_max_attempts,
        settings.rate_limit_window_seconds,
        settings.rate_limit_lockout_seconds,
    )
    store = RefreshStore()
    # One instance, created at app build time so the WARNING about a missing
    # control plane is emitted once at boot rather than on every login.
    registry = Registry(settings.registry_dsn, settings.registry_cache_ttl)
    # Credentials are held only for the lifetime of a refresh chain, never logged and never
    # returned. They are needed because Odoo's execute_kw authenticates every call.
    sessions = {}
    sessions_lock = threading.Lock()

    app = FastAPI(title="BCT login gateway", docs_url=None, redoc_url=None, openapi_url=None)
    router = APIRouter()

    def _audit(event: str, **fields) -> None:
        """Structured audit line. Never carries a credential, a token, or a personal value."""
        _logger.info(
            "audit %s %s",
            event,
            " ".join("%s=%s" % (k, v) for k, v in sorted(fields.items())),
        )

    @router.get("/healthz")
    def healthz():
        return {"status": "ok", "keys": len(ring.keys), "active_kid": ring.active_kid}

    @router.get("/metrics")
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @router.get("/.well-known/jwks.json")
    def jwks():
        """Public keys only. Two of them, always (finding T-4)."""
        return JSONResponse(
            ring.jwks(),
            headers={"Cache-Control": "public, max-age=300"},
        )

    def _set_refresh_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            settings.refresh_cookie_name,
            token,
            max_age=settings.refresh_token_ttl,
            httponly=True,        # unreadable from JavaScript, so XSS cannot exfiltrate it
            secure=settings.cookie_secure,
            samesite="strict",    # not sent on cross-site requests, so CSRF cannot spend it
            path="/auth",         # narrowest path that still covers refresh and logout
        )

    def _issue(response: Response, tenant: str, uid: int, claims: dict, password: str) -> dict:
        # The control-plane lookup happens on EVERY issue, which means on every
        # refresh as well as on login. That is deliberate: a subscription that
        # lapses mid-session must stop the next refresh, not merely the next
        # login, or a client with a long-lived refresh chain never notices.
        ent = registry.lookup(tenant)
        claims = dict(claims)
        claims["subscription_active"] = ent.active
        claims["products"] = ent.products
        token, expires = mint_access_token(settings, ring, tenant, uid, claims)
        refresh = store.issue(tenant, uid, settings.refresh_token_ttl)
        with sessions_lock:
            sessions[refresh] = password
        _set_refresh_cookie(response, refresh)
        TOKENS_ISSUED.labels(tenant=tenant).inc()
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": settings.access_token_ttl,
            "expires_at": dt.datetime.fromtimestamp(expires, dt.timezone.utc).isoformat(),
            "kid": ring.active_kid,
            "tenant_id": tenant,
            "roles": claims["roles"],
            "allowed_ou": claims["allowed_ou"],
            "all_ou": claims["all_ou"],
            # Mirrored into the response body as well as the token so the
            # portal can branch before it has decoded anything.
            "is_super_admin": bool(claims.get("is_super_admin", False)),
            "subscription_active": ent.active,
            "products": list(ent.products),
        }

    @router.post("/auth/login")
    def login(payload: LoginRequest, request: Request, response: Response):
        client_ip = request.client.host if request.client else "unknown"
        account_key = "account:%s:%s" % (payload.db, payload.login)
        source_key = "source:%s" % client_ip

        for key in (account_key, source_key):
            remaining = limiter.is_locked(key)
            if remaining:
                AUTH_TOTAL.labels(result="ratelimited").inc()
                _audit("login.ratelimited", db=payload.db, source=client_ip)
                return JSONResponse(
                    {"error": "rate_limited",
                     "detail": "Too many authentication attempts. Try again later."},
                    status_code=429,
                    headers={"Retry-After": str(int(remaining) + 1)},
                )

        if payload.db not in settings.allowed_databases:
            # Same response as bad credentials: whether a database exists is not something an
            # unauthenticated caller gets to enumerate.
            limiter.record_failure(source_key)
            AUTH_TOTAL.labels(result="invalid").inc()
            _audit("login.failed", db=payload.db, source=client_ip, reason="database")
            return _invalid()

        try:
            uid = odoo.authenticate(payload.db, payload.login, payload.password)
        except AuthenticationFailed:
            limiter.record_failure(account_key)
            limiter.record_failure(source_key)
            AUTH_TOTAL.labels(result="invalid").inc()
            _audit("login.failed", db=payload.db, source=client_ip, reason="credentials")
            return _invalid()
        except OdooError as exc:
            AUTH_TOTAL.labels(result="upstream_error").inc()
            _logger.error("login upstream failure: %s", exc)
            return JSONResponse(
                {"error": "upstream_unavailable", "detail": "Authentication backend unavailable."},
                status_code=503,
            )

        try:
            claims = read_session_claims(odoo, payload.db, uid, payload.password)
        except OdooError as exc:
            AUTH_TOTAL.labels(result="upstream_error").inc()
            _logger.error("entitlement read failed: %s", exc)
            return JSONResponse(
                {"error": "upstream_unavailable", "detail": "Authentication backend unavailable."},
                status_code=503,
            )

        limiter.record_success(account_key)
        AUTH_TOTAL.labels(result="success").inc()
        _audit("login.success", db=payload.db, uid=uid, source=client_ip,
               roles=",".join(claims["roles"]), all_ou=claims["all_ou"])
        return _issue(response, payload.db, uid, claims, payload.password)

    @router.post("/auth/refresh")
    def refresh(request: Request, response: Response):
        token = request.cookies.get(settings.refresh_cookie_name)
        if not token:
            return JSONResponse(
                {"error": "no_refresh_token", "detail": "No refresh cookie present."},
                status_code=401,
            )
        record = store.consume(token)
        with sessions_lock:
            password = sessions.pop(token, None)
        if record is None or password is None:
            _audit("refresh.rejected")
            return JSONResponse(
                {"error": "invalid_refresh_token", "detail": "Refresh token is not valid."},
                status_code=401,
            )
        try:
            # Re-read entitlements on every refresh rather than copying the old claims forward.
            # An access token lasts an hour; a session lasts two weeks. Copying claims would mean a
            # revoked Operating Unit or a removed role stayed effective for the whole session.
            claims = read_session_claims(odoo, record["tenant"], record["uid"], password)
        except OdooError:
            return JSONResponse(
                {"error": "upstream_unavailable", "detail": "Authentication backend unavailable."},
                status_code=503,
            )
        _audit("refresh.success", db=record["tenant"], uid=record["uid"])
        return _issue(response, record["tenant"], record["uid"], claims, password)

    @router.post("/auth/logout")
    def logout(request: Request, response: Response):
        token = request.cookies.get(settings.refresh_cookie_name)
        if token:
            store.revoke(token)
            with sessions_lock:
                sessions.pop(token, None)
        response.delete_cookie(
            settings.refresh_cookie_name, path="/auth",
            httponly=True, secure=settings.cookie_secure, samesite="strict",
        )
        _audit("logout")
        return {"status": "logged_out"}

    app.include_router(router)
    return app


def _invalid():
    """One response for every authentication failure.

    Deliberately identical whether the database is unknown, the login does not exist or the
    password is wrong. Distinguishing them turns the endpoint into an account and tenant
    enumeration oracle, which is the same reasoning as contract 02's 403 body not revealing
    whether the other tenant exists.
    """
    return JSONResponse(
        {"error": "invalid_credentials", "detail": "Authentication failed."},
        status_code=401,
    )


# Constant-time comparison helper kept adjacent to the auth path so it is found when needed.
compare_digest = secrets.compare_digest

app = None
