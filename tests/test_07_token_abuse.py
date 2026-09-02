"""Token abuse, and the JWKS hygiene that makes the abuse possible or impossible.

Split deliberately into two halves:

* **Gateway-side properties**, which run today: the JWKS publishes only public material, pins
  RS256, and offers two distinct keys for rotation. These are testable without any verifier.
* **Verifier-side rejections**, which need `semantic-api` running. Each attack is minted here and
  sent for real; a skip says so rather than pretending.

The positive control is not optional. "The API rejected my forged token" is worth nothing unless a
correctly signed token would have been accepted, so every rejection test in this file is paired
with an assertion that the valid token works.
"""

from __future__ import annotations

import base64
import json

import pytest

from helpers import env, tokens, web

pytestmark = [pytest.mark.live]

QUERY = {"metric": "revenue_net", "dimensions": ["date_day"],
         "filters": {"date_range": ["2026-01-01", "2026-12-31"]}, "limit": 5}


def _decode_header(token):
    header = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4)))


# ---------------------------------------------------------------------------------------------
# Gateway-side: runnable now
# ---------------------------------------------------------------------------------------------


def test_jwks_publishes_no_private_material(gateway_up, evidence):
    """The gateway holds the private key; every verifier fetches only the public half."""
    response = web.request(web.gateway_url("/.well-known/jwks.json"))
    assert response.status == 200, response.status
    document = response.json()
    evidence.add(
        "JWKS",
        json.dumps(
            [{k: (v[:32] + "..." if isinstance(v, str) and len(v) > 32 else v)
              for k, v in key.items()} for key in document["keys"]],
            indent=2,
        ),
    )
    private_members = {"d", "p", "q", "dp", "dq", "qi", "k"}
    for key in document["keys"]:
        leaked = private_members & set(key)
        assert not leaked, "the JWKS publishes private RSA parameters %r" % sorted(leaked)
        assert key["kty"] == "RSA"
        assert key["alg"] == "RS256", "a non-RS256 key is published: %r" % key.get("alg")
        assert key.get("use") == "sig"


def test_jwks_offers_two_distinct_keys_for_rotation(gateway_up, evidence):
    """Rotation is only real if the standby key is genuinely a different key.

    Two entries with the same modulus would rotate the `kid` and nothing else -- a compromised key
    would stay valid under a new name.
    """
    document = web.request(web.gateway_url("/.well-known/jwks.json")).json()
    keys = document["keys"]
    kids = [k["kid"] for k in keys]
    moduli = [k["n"] for k in keys]
    evidence.add(
        "kid / modulus fingerprint",
        "\n".join("%-20s n[:24]=%s len=%d" % (k["kid"], k["n"][:24], len(k["n"])) for k in keys),
    )
    assert len(keys) >= 2, "only %d key published; there is no standby to rotate to" % len(keys)
    assert len(set(kids)) == len(kids), "duplicate kid values: %r" % kids
    assert len(set(moduli)) == len(moduli), (
        "two published keys share a modulus: the rotation would change the kid and not the key"
    )


def test_gateway_rejects_a_forged_refresh_cookie(gateway_up, evidence):
    cookie = env.env("LOGIN_GATEWAY_REFRESH_COOKIE_NAME", "bct_refresh")
    response = web.request(
        web.gateway_url("/auth/refresh"), method="POST",
        headers={"Cookie": "%s=not-a-real-refresh-token" % cookie},
    )
    evidence.add("POST /auth/refresh with a forged cookie", "%s %s" % (response.status, response.body[:300]))
    assert response.status in (401, 403), response.status


def test_minted_attack_tokens_have_the_shape_they_claim(evidence):
    """Prove the attacks are actually the attacks, before asserting anyone rejects them.

    A test that sends a malformed string and gets a 401 has proved nothing about algorithm pinning.
    """
    if not tokens.private_key_pem():
        pytest.skip("login-gateway/secrets/jwt-private.pem is absent (NOT RUN)")

    good = tokens.valid()
    none_token = tokens.alg_none()
    hs_token = tokens.hs256_with_public_key()
    other = tokens.signed_by_a_different_key()
    tampered = tokens.tampered(good)

    evidence.add(
        "headers of the minted tokens",
        "valid        %s\nalg:none     %s\nHS256 subst. %s\nforeign key  %s\ntampered     %s"
        % (_decode_header(good), _decode_header(none_token), _decode_header(hs_token),
           _decode_header(other), _decode_header(tampered)),
    )
    assert _decode_header(good)["alg"] == "RS256"
    assert _decode_header(none_token)["alg"] == "none"
    assert none_token.endswith("."), "alg:none token must carry an empty signature"
    assert _decode_header(hs_token)["alg"] == "HS256"
    assert tampered.split(".")[:2] == good.split(".")[:2], (
        "the tampered token must differ from the valid one ONLY in its signature"
    )
    assert tampered != good


# ---------------------------------------------------------------------------------------------
# Verifier-side: needs semantic-api
# ---------------------------------------------------------------------------------------------


def _post(token):
    return web.request(
        web.semantic_url("/v1/query"), method="POST", payload=QUERY,
        headers={"Authorization": "Bearer %s" % token},
    )


def test_a_valid_token_is_accepted(semantic_up, evidence):
    """The positive control every rejection below depends on."""
    response = _post(tokens.valid())
    evidence.add("POST /v1/query with a correctly signed RS256 token",
                 "%s %s" % (response.status, response.body[:400]))
    assert response.status == 200, (
        "a correctly signed token was rejected (%s). Every rejection test in this file is "
        "meaningless until this passes." % response.status
    )


@pytest.mark.parametrize(
    "name",
    ["tampered_signature", "alg_none", "hs256_substitution", "foreign_key", "expired",
     "wrong_issuer", "wrong_audience", "no_token", "garbage"],
)
def test_abusive_tokens_are_rejected(semantic_up, evidence, name):
    good = tokens.valid()
    attack = {
        "tampered_signature": lambda: tokens.tampered(good),
        "alg_none": tokens.alg_none,
        "hs256_substitution": tokens.hs256_with_public_key,
        "foreign_key": tokens.signed_by_a_different_key,
        "expired": lambda: tokens.valid(tokens.claims(ttl=-60)),
        "wrong_issuer": lambda: tokens.valid(tokens.claims(iss="https://evil.example/")),
        "wrong_audience": lambda: tokens.valid(tokens.claims(aud="some-other-app")),
        "no_token": lambda: "",
        "garbage": lambda: "not.a.token",
    }[name]()
    response = _post(attack)
    evidence.add("%s -> HTTP %s" % (name, response.status), response.body[:300])
    assert response.status == 401, (
        "%s was answered with HTTP %s, not 401. Body: %s" % (name, response.status, response.body[:300])
    )
    assert "rows" not in response.body, "a rejected request still returned data"
