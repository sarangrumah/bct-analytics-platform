"""Gateway configuration. Every name here already exists in ``.env.example`` (contract 04 §5).

New names added by Backend, all following the reserved prefix so they sit with their siblings:
``LOGIN_GATEWAY_JWT_NEXT_KID``, ``LOGIN_GATEWAY_JWT_NEXT_PRIVATE_KEY_PATH``,
``LOGIN_GATEWAY_JWT_NEXT_PUBLIC_KEY_PATH``, ``LOGIN_GATEWAY_REFRESH_TOKEN_TTL``,
``LOGIN_GATEWAY_RATE_LIMIT_*``, ``LOGIN_GATEWAY_ODOO_URL``. Contract 04 says extend, never rename.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    issuer: str
    audience: str
    algorithm: str
    jwt_kid: str
    private_key_path: str
    next_kid: str
    next_private_key_path: str
    access_token_ttl: int
    refresh_token_ttl: int
    refresh_cookie_name: str
    odoo_url: str
    allowed_databases: tuple
    rate_limit_max_attempts: int
    rate_limit_window_seconds: int
    rate_limit_lockout_seconds: int
    cookie_secure: bool
    #: DSN for the ATHERA control plane. Empty means entitlement enforcement is
    #: OFF and every authenticated tenant is issued a token claiming every
    #: product — see app/registry.py, which says so at WARNING on every boot.
    registry_dsn: str
    registry_cache_ttl: int

    def key_paths(self) -> list:
        return [
            (self.jwt_kid, self.private_key_path),
            (self.next_kid, self.next_private_key_path),
        ]


def settings_from_env(environ: dict | None = None) -> Settings:
    env = dict(os.environ if environ is None else environ)

    algorithm = env.get("LOGIN_GATEWAY_JWT_ALGORITHM", "RS256")
    if algorithm != "RS256":
        # Contract 02 pins RS256. An HS256 gateway would put the verification secret in every
        # verifier, which is exactly the property the JWKS design exists to avoid.
        raise RuntimeError(
            "LOGIN_GATEWAY_JWT_ALGORITHM must be RS256 (frozen contract 02), got %r" % algorithm
        )

    databases = env.get("LOGIN_GATEWAY_ALLOWED_DATABASES", env.get("ODOO_DB_NAME", "bct"))

    return Settings(
        issuer=env.get("LOGIN_GATEWAY_JWT_ISSUER", "https://login-gateway.local/"),
        audience=env.get("LOGIN_GATEWAY_JWT_AUDIENCE", "insight-portal"),
        algorithm=algorithm,
        jwt_kid=env.get("LOGIN_GATEWAY_JWT_KID", ""),
        private_key_path=env.get(
            "LOGIN_GATEWAY_JWT_PRIVATE_KEY_PATH", "/run/secrets/jwt-private.pem"
        ),
        next_kid=env.get("LOGIN_GATEWAY_JWT_NEXT_KID", ""),
        next_private_key_path=env.get(
            "LOGIN_GATEWAY_JWT_NEXT_PRIVATE_KEY_PATH", "/run/secrets/jwt-next-private.pem"
        ),
        access_token_ttl=int(env.get("LOGIN_GATEWAY_ACCESS_TOKEN_TTL", "3600")),
        refresh_token_ttl=int(env.get("LOGIN_GATEWAY_REFRESH_TOKEN_TTL", "1209600")),
        refresh_cookie_name=env.get("LOGIN_GATEWAY_REFRESH_COOKIE_NAME", "bct_refresh"),
        odoo_url=env.get("LOGIN_GATEWAY_ODOO_URL", "http://odoo:8069"),
        allowed_databases=tuple(d.strip() for d in databases.split(",") if d.strip()),
        rate_limit_max_attempts=int(env.get("LOGIN_GATEWAY_RATE_LIMIT_MAX_ATTEMPTS", "5")),
        rate_limit_window_seconds=int(env.get("LOGIN_GATEWAY_RATE_LIMIT_WINDOW_SECONDS", "300")),
        rate_limit_lockout_seconds=int(env.get("LOGIN_GATEWAY_RATE_LIMIT_LOCKOUT_SECONDS", "900")),
        # Defaults to True. A refresh cookie without Secure is sent over plaintext HTTP, and the
        # default must be the safe one: an operator who forgets this variable should get a cookie
        # that fails on http rather than one that silently travels in the clear.
        cookie_secure=env.get("LOGIN_GATEWAY_COOKIE_SECURE", "1") not in ("0", "false", "no"),
        registry_dsn=env.get("LOGIN_GATEWAY_REGISTRY_DSN", ""),
        # Short on purpose. This is the window in which a just-suspended tenant
        # can still mint a session by refreshing, so it trades staleness for
        # load on the control plane and should stay in the tens of seconds.
        registry_cache_ttl=int(env.get("LOGIN_GATEWAY_REGISTRY_CACHE_TTL", "30")),
    )
