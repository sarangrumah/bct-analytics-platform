"""JWT verification against the login gateway's JWKS. Server-side only.

Frozen contract 02, verification section. Three properties this module exists to guarantee:

* **The algorithm is pinned to RS256.** ``alg: none`` and HS256 confusion are rejected outright,
  and not by inspecting the header — by never handing PyJWT a symmetric algorithm or an empty
  ``algorithms`` list. An HS256 token signed with the *public key as the HMAC secret* is the classic
  confusion attack, and it only works if the verifier lets the token choose the algorithm.
* **The key is selected by ``kid``** (finding T-4). With two keys published, a verifier that just
  "tries the only key" cannot survive a rotation. Selecting by ``kid`` means the standby key works
  the moment the gateway starts signing with it, with no change here.
* **``tenant_id`` comes only from the verified token.** There is no code path in this service that
  reads a tenant from a header, query string, cookie or body.
"""

from __future__ import annotations

import logging
import threading
import time

import jwt
from jwt import PyJWKClient

_logger = logging.getLogger(__name__)


class TokenRejected(Exception):
    """The token is not usable. Always a 401; the reason is logged, not returned."""


class Session:
    """The verified claim set. Constructed only from a validated token."""

    def __init__(self, claims: dict) -> None:
        self.claims = claims
        self.tenant_id = claims["tenant_id"]
        self.odoo_uid = claims.get("odoo_uid")
        self.subject = claims.get("sub")
        self.roles = list(claims.get("roles") or [])
        self.allowed_ou = list(claims.get("allowed_ou") or [])
        # Absent means false. Ruling a0fbb88: the bypass must be explicit, so a token that predates
        # the claim, or one a bug forgot to populate, grants nothing rather than everything.
        self.all_ou = bool(claims.get("all_ou", False))

    def __repr__(self) -> str:
        return "<Session tenant=%s uid=%s all_ou=%s>" % (self.tenant_id, self.odoo_uid, self.all_ou)


class Verifier:
    def __init__(self, jwks_url: str, issuer: str, audience: str, leeway: int = 30,
                 cache_seconds: int = 300) -> None:
        self.jwks_url = jwks_url
        self.issuer = issuer
        self.audience = audience
        self.leeway = leeway
        self._client = PyJWKClient(jwks_url, cache_keys=True, lifespan=cache_seconds)
        self._lock = threading.Lock()
        self._last_refresh = 0.0
        #: Has this verifier EVER seen a key from the configured JWKS URL? Distinguishing "the
        #: endpoint has never answered" from "this token's kid is unknown" is the whole point of
        #: this flag: both used to produce the identical 401 and the identical log line, so a
        #: misconfigured SEMANTIC_API_JWKS_URL was indistinguishable from a forged token. Proven
        #: live: `login-gateway` does not resolve on the compose network -- only the container
        #: name `odoo19-bct-login-gateway` does -- and .env.example still ships the former, so a
        #: fresh clone rejects every valid token while reporting a client-side problem.
        self._jwks_ever_loaded = False

    def _signing_key(self, token: str):
        try:
            key = self._client.get_signing_key_from_jwt(token).key
            self._jwks_ever_loaded = True
            return key
        except Exception:
            # A kid that is not in the cached JWKS is the normal signal that the gateway has
            # rotated. Refresh once, at most every 10 s, then give up -- an unbounded refresh on
            # every bad token would let an attacker drive JWKS fetches.
            with self._lock:
                if time.time() - self._last_refresh > 10:
                    self._last_refresh = time.time()
                    try:
                        self._client.fetch_data()
                        self._jwks_ever_loaded = True
                    except Exception as exc:  # pragma: no cover - network dependent
                        _logger.error(
                            "JWKS fetch from %s failed: %s: %s. Every token will be rejected "
                            "until this URL is reachable, and the rejection looks exactly like a "
                            "bad token to the caller. Check SEMANTIC_API_JWKS_URL -- on the "
                            "compose network the gateway answers to its CONTAINER name "
                            "(odoo19-bct-login-gateway), not to a compose service name.",
                            self.jwks_url, exc.__class__.__name__, exc,
                        )
            try:
                key = self._client.get_signing_key_from_jwt(token).key
                self._jwks_ever_loaded = True
                return key
            except Exception:
                if not self._jwks_ever_loaded:
                    # Deliberately a different message from the kid case. The client still gets a
                    # 401 either way -- config detail must not leak -- but the SERVER-side text an
                    # operator reads now names the real cause instead of blaming the token.
                    raise TokenRejected(
                        "JWKS at %s has never yielded a key; this is a server configuration "
                        "fault, not a bad token" % self.jwks_url
                    ) from None
                raise TokenRejected("No JWKS key matches the token's kid") from None

    def verify(self, token: str) -> Session:
        if not token or not isinstance(token, str):
            raise TokenRejected("No bearer token")

        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise TokenRejected("Malformed token header: %s" % exc.__class__.__name__) from None

        # Refuse before touching keys. PyJWT would reject these anyway because `algorithms` is
        # pinned below, but failing here makes the intent explicit and keeps `alg: none` from ever
        # reaching key selection.
        if header.get("alg") != "RS256":
            raise TokenRejected("Algorithm %r is not RS256" % header.get("alg"))
        if not header.get("kid"):
            raise TokenRejected("Token carries no kid; cannot select a key (finding T-4)")

        key = self._signing_key(token)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],       # pinned; the token does not get to choose
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway,
                options={
                    "require": ["exp", "iat", "iss", "aud", "sub"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except jwt.PyJWTError as exc:
            raise TokenRejected("%s" % exc.__class__.__name__) from None

        tenant = claims.get("tenant_id")
        if not tenant or not isinstance(tenant, str):
            raise TokenRejected("Token carries no tenant_id")
        return Session(claims)
