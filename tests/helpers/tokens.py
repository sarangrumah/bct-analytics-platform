"""Mint the tokens the abuse tests need, including the malicious ones.

The gateway's signing key is used to mint the **valid** token, because a negative test is only
meaningful next to a positive control: "the API rejected my token" proves nothing if a correct
token would also have been rejected.

The three attacks are the ones contract 02 names, and each fails differently if the verifier is
wrong:

* **Tampered signature** -- the ordinary forgery. Caught by any signature check.
* **``alg: none``** -- the token declares it is unsigned. Caught only if the verifier pins the
  algorithm instead of trusting the header.
* **HS256 substitution** -- the token is signed with HMAC using the *public* key as the shared
  secret. The public key is, by design, public. A verifier that reads ``alg`` from the header and
  looks up "the key" will hand a public RSA key to an HMAC verifier and the signature validates.
  This is the classic JWT confusion attack and it is why contract 02 says "pinned to RS256".

PyJWT refuses to *encode* ``alg: none`` and refuses to use an RSA public key as an HMAC secret, so
both of those are assembled by hand from base64url segments. That is the point: an attacker is not
constrained by a library's guard rails, so a test that relies on one is not testing the attack.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from .env import env, repo_root


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _segment(obj) -> str:
    return _b64(json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def private_key_pem() -> str:
    path = repo_root() / "login-gateway" / "secrets" / "jwt-private.pem"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def public_key_pem() -> str:
    path = repo_root() / "login-gateway" / "secrets" / "jwt-public.pem"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def claims(tenant="bct", uid=2, roles=None, allowed_ou=None, all_ou=False, ttl=3600, **overrides):
    """A contract-02 claim set. `allowed_ou` defaults to `[]`, which now means NO operating units."""
    now = int(time.time())
    payload = {
        "iss": env("LOGIN_GATEWAY_JWT_ISSUER", "https://login-gateway.local/"),
        "aud": env("LOGIN_GATEWAY_JWT_AUDIENCE", "insight-portal"),
        "sub": "odoo:%s:%d" % (tenant, uid),
        "tenant_id": tenant,
        "odoo_uid": uid,
        "roles": roles if roles is not None else ["analytics.viewer"],
        "allowed_ou": [] if allowed_ou is None else allowed_ou,
        "all_ou": all_ou,
        "company_ids": [1],
        "iat": now,
        "exp": now + ttl,
    }
    payload.update(overrides)
    return payload


def valid(payload=None, kid=None) -> str:
    import jwt  # PyJWT

    key = private_key_pem()
    if not key:
        raise RuntimeError("login-gateway/secrets/jwt-private.pem is absent; cannot mint a token")
    headers = {"kid": kid or env("LOGIN_GATEWAY_JWT_KID", "")}
    return jwt.encode(payload or claims(), key, algorithm="RS256", headers=headers)


def tampered(token: str) -> str:
    """Flip one byte of the signature. Header and payload stay byte-identical."""
    header, payload, signature = token.split(".")
    raw = bytearray(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)))
    raw[0] ^= 0xFF
    return "%s.%s.%s" % (header, payload, _b64(bytes(raw)))


def alg_none(payload=None, kid=None) -> str:
    """``{"alg":"none"}`` with an empty signature, assembled by hand."""
    header = {"alg": "none", "typ": "JWT"}
    if kid or env("LOGIN_GATEWAY_JWT_KID", ""):
        header["kid"] = kid or env("LOGIN_GATEWAY_JWT_KID", "")
    return "%s.%s." % (_segment(header), _segment(payload or claims()))


def hs256_with_public_key(payload=None, kid=None) -> str:
    """Sign with HMAC-SHA256 using the RSA **public** key bytes as the shared secret."""
    header = {"alg": "HS256", "typ": "JWT"}
    if kid or env("LOGIN_GATEWAY_JWT_KID", ""):
        header["kid"] = kid or env("LOGIN_GATEWAY_JWT_KID", "")
    signing_input = "%s.%s" % (_segment(header), _segment(payload or claims()))
    secret = public_key_pem().encode("utf-8")
    signature = hmac.new(secret, signing_input.encode("ascii"), hashlib.sha256).digest()
    return "%s.%s" % (signing_input, _b64(signature))


def signed_by_a_different_key(payload=None) -> str:
    """A perfectly well-formed RS256 token signed by a key the JWKS does not publish."""
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    return jwt.encode(payload or claims(), pem, algorithm="RS256",
                      headers={"kid": env("LOGIN_GATEWAY_JWT_KID", "")})
