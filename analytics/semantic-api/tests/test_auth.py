"""Token verification: acceptance criterion 9, plus the T-4 kid-selection behaviour.

A tampered token, an ``alg: none`` token and an HS256-signed token must all be rejected. The HS256
case is the one that is easy to get wrong: the classic algorithm-confusion attack signs a token with
the verifier's own **public key used as an HMAC secret**, and it succeeds against any verifier that
lets the token choose its algorithm. These tests construct that exact token rather than a generic
"wrong algorithm" one.
"""

from __future__ import annotations

import datetime as dt
import json

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import TokenRejected, Verifier

ISSUER = "https://login-gateway.local/"
AUDIENCE = "insight-portal"


def _pem(key):
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _public_pem(key):
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


@pytest.fixture(scope="module")
def keys():
    return {
        "kid-active": rsa.generate_private_key(public_exponent=65537, key_size=2048),
        "kid-standby": rsa.generate_private_key(public_exponent=65537, key_size=2048),
        "kid-foreign": rsa.generate_private_key(public_exponent=65537, key_size=2048),
    }


@pytest.fixture
def verifier(keys, monkeypatch):
    """A Verifier whose JWKS holds the two published keys — never the foreign one."""
    import jwt as pyjwt

    published = {}
    for kid in ("kid-active", "kid-standby"):
        jwk = json.loads(pyjwt.algorithms.RSAAlgorithm.to_jwk(keys[kid].public_key()))
        jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
        published[kid] = jwk

    verifier = Verifier.__new__(Verifier)
    verifier.jwks_url = "http://stub/.well-known/jwks.json"
    verifier.issuer = ISSUER
    verifier.audience = AUDIENCE
    verifier.leeway = 30
    verifier._lock = __import__("threading").Lock()
    verifier._last_refresh = 0.0

    class _StubClient:
        def get_signing_key_from_jwt(self, token):
            kid = pyjwt.get_unverified_header(token)["kid"]
            if kid not in published:
                raise KeyError(kid)
            return type("K", (), {"key": pyjwt.algorithms.RSAAlgorithm.from_jwk(
                json.dumps(published[kid])
            )})()

        def fetch_data(self):
            return None

    verifier._client = _StubClient()
    return verifier


def claims(**overrides):
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "iss": ISSUER, "aud": AUDIENCE, "sub": "odoo:bct:7",
        "tenant_id": "bct", "odoo_uid": 7, "roles": ["analytics.viewer"],
        "allowed_ou": [1], "all_ou": False, "company_ids": [1],
        "iat": int(now.timestamp()), "exp": int((now + dt.timedelta(hours=1)).timestamp()),
    }
    payload.update(overrides)
    return payload


def test_a_valid_token_verifies(verifier, keys):
    token = jwt.encode(claims(), _pem(keys["kid-active"]), algorithm="RS256",
                       headers={"kid": "kid-active"})
    session = verifier.verify(token)
    assert session.tenant_id == "bct"
    assert session.all_ou is False


def test_the_standby_key_also_verifies(verifier, keys):
    """T-4: the standby key works the moment the gateway starts signing with it."""
    token = jwt.encode(claims(), _pem(keys["kid-standby"]), algorithm="RS256",
                       headers={"kid": "kid-standby"})
    assert verifier.verify(token).tenant_id == "bct"


def test_alg_none_is_rejected(verifier):
    token = jwt.encode(claims(), key="", algorithm="none", headers={"kid": "kid-active"})
    with pytest.raises(TokenRejected):
        verifier.verify(token)


def test_hs256_algorithm_confusion_is_rejected(verifier, keys):
    """The real attack: sign with the verifier's PUBLIC key used as an HMAC secret.

    Hand-assembled rather than built with ``jwt.encode``, because PyJWT refuses to *create* this
    token -- it rejects a PEM as an HMAC secret. That refusal protects the signer, not the
    verifier, so using it as the test would only prove PyJWT declines to help. An attacker has no
    such scruples and will assemble the bytes directly, which is what this does.
    """
    import base64
    import hashlib
    import hmac as hmac_mod

    public_pem = _public_pem(keys["kid-active"])

    def b64(raw):
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "kid-active"}).encode())
    payload = b64(json.dumps(claims()).encode())
    signing_input = ("%s.%s" % (header, payload)).encode()
    signature = b64(hmac_mod.new(public_pem, signing_input, hashlib.sha256).digest())
    forged = "%s.%s.%s" % (header, payload, signature)

    # Sanity: the forgery IS well-formed and would verify under a permissive verifier.
    assert jwt.get_unverified_header(forged)["alg"] == "HS256"

    with pytest.raises(TokenRejected):
        verifier.verify(forged)


def test_a_token_signed_by_an_unpublished_key_is_rejected(verifier, keys):
    token = jwt.encode(claims(), _pem(keys["kid-foreign"]), algorithm="RS256",
                       headers={"kid": "kid-foreign"})
    with pytest.raises(TokenRejected):
        verifier.verify(token)


def test_a_token_with_a_known_kid_but_a_foreign_signature_is_rejected(verifier, keys):
    """Claiming a published kid does not help if the signature was made by another key."""
    token = jwt.encode(claims(), _pem(keys["kid-foreign"]), algorithm="RS256",
                       headers={"kid": "kid-active"})
    with pytest.raises(TokenRejected):
        verifier.verify(token)


def test_a_tampered_payload_is_rejected(verifier, keys):
    import base64
    token = jwt.encode(claims(), _pem(keys["kid-active"]), algorithm="RS256",
                       headers={"kid": "kid-active"})
    header, payload, signature = token.split(".")
    decoded = json.loads(base64.urlsafe_b64decode(payload + "=="))
    decoded["tenant_id"] = "someone_else"
    tampered = base64.urlsafe_b64encode(
        json.dumps(decoded).encode()
    ).decode().rstrip("=")
    with pytest.raises(TokenRejected):
        verifier.verify(".".join([header, tampered, signature]))


def test_a_token_with_no_kid_is_rejected(verifier, keys):
    """T-4 depends on kid selection; a token without one cannot be routed to a key."""
    token = jwt.encode(claims(), _pem(keys["kid-active"]), algorithm="RS256")
    with pytest.raises(TokenRejected):
        verifier.verify(token)


def test_wrong_audience_and_issuer_are_rejected(verifier, keys):
    for override in ({"aud": "someone-else"}, {"iss": "https://evil.invalid/"}):
        token = jwt.encode(claims(**override), _pem(keys["kid-active"]),
                           algorithm="RS256", headers={"kid": "kid-active"})
        with pytest.raises(TokenRejected):
            verifier.verify(token)


def test_an_expired_token_is_rejected(verifier, keys):
    past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
    token = jwt.encode(
        claims(exp=int(past.timestamp()), iat=int((past - dt.timedelta(hours=1)).timestamp())),
        _pem(keys["kid-active"]), algorithm="RS256", headers={"kid": "kid-active"},
    )
    with pytest.raises(TokenRejected):
        verifier.verify(token)


def test_absent_all_ou_defaults_to_false(verifier, keys):
    """Ruling a0fbb88: the bypass is explicit. A token missing the claim grants nothing."""
    payload = claims()
    del payload["all_ou"]
    token = jwt.encode(payload, _pem(keys["kid-active"]), algorithm="RS256",
                       headers={"kid": "kid-active"})
    assert verifier.verify(token).all_ou is False


def test_a_token_without_tenant_id_is_rejected(verifier, keys):
    payload = claims()
    del payload["tenant_id"]
    token = jwt.encode(payload, _pem(keys["kid-active"]), algorithm="RS256",
                       headers={"kid": "kid-active"})
    with pytest.raises(TokenRejected):
        verifier.verify(token)
