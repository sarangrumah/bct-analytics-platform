"""The PDP digest, reproduced byte-identically from ``custom_pdp_masking``.

This file is a *copy of a specification*, not an independent implementation. Frozen contract 01 and
``addons/custom_pdp_masking/MODULE_KNOWLEDGE.md`` §2 pin every degree of freedom:

===  ==========================================================================================
 1   primitive is HMAC (RFC 2104), not a plain hash and not a salt concatenation
 2   digest is SHA-256
 3   the salt is the HMAC **key**; the value is the **message** -- ``HMAC(key=salt, msg=value)``
 4   key encoding is UTF-8
 5   message encoding is UTF-8
 6   **no normalisation** -- no trim, no case fold, no Unicode NFC/NFD
 7   output is ``hexdigest()``: exactly 64 lowercase ``[0-9a-f]`` characters
 8   ``None`` in -> ``None`` out; NULL is preserved, never hashed to a constant
 9   ``""`` in -> ``None`` out; hashing the empty string fabricates a shared join key
10   non-``str`` input raises ``TypeError``; there is no cross-language-safe coercion
11   an empty or absent salt raises ``ValueError``; never degrade to an unkeyed hash
===  ==========================================================================================

If this file and the Odoo module ever disagree, joins break *silently* -- the failure surfaces much
later as a reconciliation mismatch, not as an error. That is why :mod:`bct_cdc.odoo_rpc` asserts
``pdp.masking.rule.get_digest_spec()`` over JSON-RPC at loader startup, and why the known-answer
vectors below are asserted by the test suite on both sides.
"""

from __future__ import annotations

import hashlib
import hmac

#: Advertised algorithm name. Must equal ``pdp_hash.PDP_DIGEST_ALGORITHM`` in the Odoo module
#: verbatim -- it names the primitive, the encoding and the hex casing in one string, and the
#: startup check compares it exactly rather than by prefix. The first run of that check caught this
#: constant reading ``hmac-sha256``, which is why it compares exactly.
PDP_DIGEST_ALGORITHM = "hmac-sha256/utf8/lowerhex"

#: The four known-answer vectors from MODULE_KNOWLEDGE.md §2. Changing any of these invalidates
#: every digest already in the warehouse: it is a migration, not a bug fix.
KNOWN_ANSWER_VECTORS = (
    (
        "budi.santoso@contoh.invalid",
        "bct-demo-salt",
        "57890775652c2e05536c54638d280c1f2cde752d0fc52bf42ac3a76d53ddbd5e",
    ),
    (
        "budi.santoso@contoh.invalid",
        "other-tenant-salt",
        "c24c6fc738f518543fe3b5cfb2e8e0bafd0464371333e91188552317f9b4f738",
    ),
    (
        "Budi Santoso",
        "bct-demo-salt",
        "a5c30f115ac845dd0cfafabe0326c71de7f1e7d3a869d252c4caa894ab4b978b",
    ),
    (
        "Ir. Sri Wahyuni, S.T.",
        "bct-demo-salt",
        "9a5f1b855c3e59c66e701fb93f6411627790d573b9d48cda9c7b74cf1a1e6b3b",
    ),
)


def pdp_hmac_sha256(value: str | None, salt: str) -> str | None:
    """Return the deterministic 64-character lowercase hex digest of ``value``.

    Copied verbatim from ``addons/custom_pdp_masking/models/pdp_hash.py``.
    """
    if not isinstance(salt, str):
        raise TypeError("PDP salt must be a str")
    if not salt:
        raise ValueError("PDP salt is empty")
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("PDP digest input must be str or None")
    if value == "":
        return None
    return hmac.new(salt.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def self_test() -> None:
    """Raise ``AssertionError`` unless every known-answer vector reproduces.

    Called at loader startup *before* any connection is opened, so a broken build cannot reach the
    database at all.
    """
    for value, salt, expected in KNOWN_ANSWER_VECTORS:
        got = pdp_hmac_sha256(value, salt)
        if got != expected:
            raise AssertionError(
                f"PDP digest self-test failed for {value!r} with the test salt: "
                f"expected {expected}, got {got}"
            )
