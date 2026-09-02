"""An independent re-implementation of the PDP digest, written from the specification.

This deliberately does **not** import ``bct_cdc.pdp_hash``. Importing the loader's own code would
make the masking test a tautology: it would assert that a function agrees with itself. The eleven
rules below are transcribed from ``addons/custom_pdp_masking/MODULE_KNOWLEDGE.md`` §2, which is the
authority named by contract 05, and the four known-answer vectors from §2 are asserted by
``test_04_masking.py`` before any warehouse value is compared -- so if this file drifts from the
specification, the test fails here rather than producing a false mismatch downstream.
"""

from __future__ import annotations

import hashlib
import hmac

#: MODULE_KNOWLEDGE.md §2, transcribed. value, salt, expected digest.
KNOWN_ANSWER_VECTORS = (
    ("budi.santoso@contoh.invalid", "bct-demo-salt",
     "57890775652c2e05536c54638d280c1f2cde752d0fc52bf42ac3a76d53ddbd5e"),
    ("budi.santoso@contoh.invalid", "other-tenant-salt",
     "c24c6fc738f518543fe3b5cfb2e8e0bafd0464371333e91188552317f9b4f738"),
    ("Budi Santoso", "bct-demo-salt",
     "a5c30f115ac845dd0cfafabe0326c71de7f1e7d3a869d252c4caa894ab4b978b"),
    ("Ir. Sri Wahyuni, S.T.", "bct-demo-salt",
     "9a5f1b855c3e59c66e701fb93f6411627790d573b9d48cda9c7b74cf1a1e6b3b"),
)


def digest(value, salt: str):
    """HMAC-SHA256(key=salt, msg=value), lowercase hex. NULL and "" both yield None."""
    if not isinstance(salt, str) or salt == "":
        raise ValueError("PDP salt must be a non-empty str")
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise TypeError("PDP digest input must be str or None")
    return hmac.new(salt.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def salt_concat_sha256(value: str, salt: str) -> str:
    """The wrong construction, kept as a negative control.

    MODULE_KNOWLEDGE.md §2 asserts `sha256(salt + value)` must **not** equal the digest. Without
    this, a loader that silently used salt-concatenation would still produce 64 lowercase hex
    characters and pass every shape check.
    """
    return hashlib.sha256((salt + value).encode("utf-8")).hexdigest()
