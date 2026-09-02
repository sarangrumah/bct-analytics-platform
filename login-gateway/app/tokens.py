"""Minting the contract 02 access token and the refresh token.

The access token's claim set is frozen. This module is the only place it is constructed, so
"which claims does a session carry" has exactly one answer in the codebase.
"""

from __future__ import annotations

import datetime as dt
import secrets

import jwt


def _now() -> int:
    return int(dt.datetime.now(dt.timezone.utc).timestamp())


def mint_access_token(settings, ring, tenant: str, uid: int, claims: dict) -> tuple:
    """Return ``(token, expires_at)`` for the contract 02 access token.

    ``kid`` is always written into the header. That is what makes T-4's rotation work: a verifier
    picks the key by ``kid`` rather than by "the only key there is", so the standby key in JWKS is
    usable the moment the gateway is reconfigured to sign with it.
    """
    issued = _now()
    expires = issued + settings.access_token_ttl
    payload = {
        "iss": settings.issuer,
        "aud": settings.audience,
        "sub": "odoo:%s:%d" % (tenant, uid),
        "tenant_id": tenant,
        "odoo_uid": uid,
        "roles": claims["roles"],
        "allowed_ou": claims["allowed_ou"],
        # Contract 02, GATE 3 amendment. Always written explicitly, never left to be inferred from
        # an empty allowed_ou -- the whole point of the amendment is that a forgotten claim grants
        # nothing rather than everything.
        "all_ou": bool(claims.get("all_ou", False)),
        "company_ids": claims["company_ids"],
        # --- ATHERA, 2026-09-01 -------------------------------------------
        # The diagram's two decision diamonds, as claims. Both are written
        # explicitly and both default to the DENYING value, for the same reason
        # the GATE 3 amendment made all_ou explicit: a claim that is absent
        # must grant nothing, never everything. A verifier that forgets to read
        # `subscription_active` still sees `false` if it was false.
        "is_super_admin": bool(claims.get("is_super_admin", False)),
        "subscription_active": bool(claims.get("subscription_active", False)),
        # Which of insight/odoo/agent this tenant's plan includes. Empty is a
        # valid and meaningful answer: an unknown or unplanned tenant gets no
        # product, which is what tenant_registry.entitlements() returns for one.
        "products": list(claims.get("products", ())),
        "iat": issued,
        "exp": expires,
    }
    key = ring.active
    token = jwt.encode(
        payload,
        key.private_pem,
        algorithm="RS256",
        headers={"kid": key.kid, "typ": "JWT"},
    )
    return token, expires


def mint_refresh_token() -> str:
    """An opaque, high-entropy refresh token.

    Deliberately **not** a JWT. A self-contained refresh token cannot be revoked before it expires,
    which makes logout a lie: the browser forgets the cookie and the token keeps working for anyone
    who captured it. An opaque handle is revocable because the server holds the state.
    """
    return secrets.token_urlsafe(48)
