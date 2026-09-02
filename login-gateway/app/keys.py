"""RS256 signing keys and the JWKS endpoint — security finding T-4.

**Two keys are published from day one.** Not because two are needed today, but because a one-key
JWKS is a design that cannot be rotated without downtime. With a single key, rotation is:

    publish the new key -> every verifier that cached the old JWKS now rejects every token
    -> or sign with the new key before verifiers have it -> every verifier rejects every token

Either order is an outage. There is no sequence of two steps that avoids it, which is why the fix
has to be in the *shape* of the deployment rather than in the rotation procedure.

With two keys published and `kid` selecting between them, rotation is a config change:

1. Both keys are already in JWKS and every verifier already accepts both.
2. Flip ``LOGIN_GATEWAY_JWT_KID`` to the second key and restart the gateway.
3. Tokens signed by the old key keep verifying until they expire (3600 s).
4. Generate a fresh "next" key at leisure and repeat.

At no point is a key used for signing before verifiers have seen it, and at no point is a key
removed while tokens signed by it are still alive.

**The gateway holds private keys; verifiers hold none.** Contract 02: verifiers fetch the public
half from JWKS. Nothing in this module ever serialises a private key into a response — the JWKS
payload is built from public numbers only, and :func:`_public_jwk` takes the public key object
rather than the private one so that the mistake is not expressible.
"""

from __future__ import annotations

import base64
import logging

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_logger = logging.getLogger(__name__)


class KeyConfigurationError(RuntimeError):
    """The gateway cannot start with the keys it was given."""


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _public_jwk(public_key: rsa.RSAPublicKey, kid: str, use_now: bool) -> dict:
    """Build one JWKS entry. Takes the PUBLIC key object, so a private key cannot be passed here."""
    numbers = public_key.public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _b64url_uint(numbers.n),
        "e": _b64url_uint(numbers.e),
        # Not part of RFC 7517; purely informational for an operator reading the endpoint, so that
        # "which key am I actually signing with" is answerable without shelling into the container.
        "x-bct-status": "active" if use_now else "standby",
    }


class SigningKey:
    def __init__(self, kid: str, private_pem: bytes) -> None:
        self.kid = kid
        self._private = serialization.load_pem_private_key(private_pem, password=None)
        if not isinstance(self._private, rsa.RSAPrivateKey):
            raise KeyConfigurationError("Key %s is not an RSA key; contract 02 pins RS256" % kid)
        if self._private.key_size < 2048:
            raise KeyConfigurationError(
                "Key %s is %d bits; RS256 signing keys must be at least 2048."
                % (kid, self._private.key_size)
            )
        self.public = self._private.public_key()

    @property
    def private_pem(self) -> bytes:
        """The signing key itself. Used only by :mod:`app.tokens`; never reachable over HTTP."""
        return self._private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )


class KeyRing:
    """The active signing key plus every key verifiers must still accept."""

    def __init__(self, keys: list, active_kid: str) -> None:
        if len(keys) < 2:
            raise KeyConfigurationError(
                "Only %d signing key(s) configured. Security finding T-4 requires TWO keys in JWKS "
                "from day one: a single-key JWKS cannot be rotated without a flag-day outage, "
                "because there is no ordering of 'publish the new key' and 'sign with the new key' "
                "that does not reject live tokens. Set LOGIN_GATEWAY_JWT_NEXT_KID and "
                "LOGIN_GATEWAY_JWT_NEXT_PRIVATE_KEY_PATH." % len(keys)
            )
        kids = [k.kid for k in keys]
        if len(set(kids)) != len(kids):
            raise KeyConfigurationError(
                "Duplicate kid in the key ring (%s). Two keys sharing a kid defeats the point: a "
                "verifier cannot tell them apart, so rotation is still a flag day." % ", ".join(kids)
            )
        if active_kid not in kids:
            raise KeyConfigurationError(
                "LOGIN_GATEWAY_JWT_KID=%r is not one of the configured kids (%s)."
                % (active_kid, ", ".join(kids))
            )
        moduli = {k.public.public_numbers().n for k in keys}
        if len(moduli) != len(keys):
            raise KeyConfigurationError(
                "Two keys in the ring share a modulus, i.e. the same key was configured twice under "
                "different kids. That looks like a rotation story and is not one."
            )
        self.keys = {k.kid: k for k in keys}
        self.active_kid = active_kid

    @property
    def active(self) -> SigningKey:
        return self.keys[self.active_kid]

    def jwks(self) -> dict:
        return {
            "keys": [
                _public_jwk(key.public, kid, use_now=(kid == self.active_kid))
                for kid, key in self.keys.items()
            ]
        }


def load_key_ring(settings) -> KeyRing:
    keys = []
    for kid, path in settings.key_paths():
        try:
            with open(path, "rb") as handle:
                pem = handle.read()
        except OSError as exc:
            raise KeyConfigurationError(
                "Cannot read signing key %s at %s: %s" % (kid, path, exc)
            ) from exc
        keys.append(SigningKey(kid, pem))
    ring = KeyRing(keys, settings.jwt_kid)
    _logger.info(
        "JWKS will publish %d keys (active=%s, standby=%s)",
        len(ring.keys),
        ring.active_kid,
        ", ".join(k for k in ring.keys if k != ring.active_kid),
    )
    return ring
