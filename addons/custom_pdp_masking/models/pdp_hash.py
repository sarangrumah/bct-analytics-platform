# Part of custom_pdp_masking. Licence: LGPL-3.
"""Reference implementation of the PDP deterministic digest.

THIS FILE IS A CROSS-LANGUAGE CONTRACT.
=======================================

The Phase 3 CDC loader is a separate Python process; dbt and any future non-Python consumer must
be able to reproduce the same digest for the same input. "Same" means byte-identical output, so
every degree of freedom is nailed down here and repeated in ``MODULE_KNOWLEDGE.md``:

1.  Primitive        : HMAC (RFC 2104) with SHA-256.
2.  Key              : the per-tenant salt. The salt is the HMAC **key**, NOT a prefix or a suffix
                       concatenated onto the value. ``HMAC(key=salt, message=value)``.
3.  Key encoding     : UTF-8 of the salt string, no normalisation, no trimming.
4.  Message encoding : UTF-8 of the value string, no normalisation, no trimming, no case folding.
5.  Output           : ``hexdigest()`` - 64 characters, lowercase ``0-9a-f``.
6.  NULL             : ``None`` in, ``None`` out. NULL is preserved, never hashed into a constant.
7.  Empty string     : ``""`` in, ``None`` out. An empty string carries no personal data and
                       hashing it would give every empty cell the same non-NULL digest, which
                       fabricates a join key.
8.  Non-text input   : rejected with ``TypeError``. The caller must decide how a number or a date
                       becomes text; there is no cross-language-safe implicit conversion
                       (Python ``str(1.0)`` is ``'1.0'``, other runtimes disagree). In practice no
                       numeric column is classified ``personal``; the ones that are personal-ish
                       (coordinates) carry ``drop_to_null`` instead.
9.  Empty salt       : rejected with ``ValueError``. An unset salt must fail loudly, never silently
                       degrade to an unkeyed hash.

Known-answer vector (asserted by ``tests/test_pdp_hash.py`` and repeated in MODULE_KNOWLEDGE.md):

    value = "budi.santoso@contoh.invalid"
    salt  = "bct-demo-salt"
    ->      57890775652c2e05536c54638d280c1f2cde752d0fc52bf42ac3a76d53ddbd5e

    value = "budi.santoso@contoh.invalid"
    salt  = "other-tenant-salt"
    ->      c24c6fc738f518543fe3b5cfb2e8e0bafd0464371333e91188552317f9b4f738

    value = "Budi Santoso"
    salt  = "bct-demo-salt"
    ->      a5c30f115ac845dd0cfafabe0326c71de7f1e7d3a869d252c4caa894ab4b978b

    value = "Ir. Sri Wahyuni, S.T."     (non-ASCII-safe check: punctuation and spacing preserved)
    salt  = "bct-demo-salt"
    ->      9a5f1b855c3e59c66e701fb93f6411627790d573b9d48cda9c7b74cf1a1e6b3b

Reimplementation in plain Python, for the loader::

    import hashlib, hmac
    def pdp_hmac_sha256(value, salt):
        if value is None or value == "":
            return None
        return hmac.new(salt.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()

Rotating a salt invalidates every historical join built on its digests. Treat a rotation as a
warehouse migration, not a config change (contract 01).
"""

import hashlib
import hmac

__all__ = ["PDP_DIGEST_ALGORITHM", "PDP_DIGEST_LENGTH", "pdp_hmac_sha256"]

#: Name of the digest, for logging and for the loader's self-check.
PDP_DIGEST_ALGORITHM = "hmac-sha256/utf8/lowerhex"

#: Length in characters of the hex output. A shorter or longer value is a bug, not a variant.
PDP_DIGEST_LENGTH = 64


def pdp_hmac_sha256(value, salt):
    """Return the deterministic PDP digest of ``value`` under ``salt``.

    :param value: the cleartext, as ``str``, or ``None``.
    :param salt: the per-tenant salt, as a non-empty ``str``.
    :return: 64-character lowercase hex ``str``, or ``None`` when ``value`` is ``None`` or empty.
    :raises TypeError: when ``value`` is neither ``str`` nor ``None``, or ``salt`` is not ``str``.
    :raises ValueError: when ``salt`` is empty.
    """
    if not isinstance(salt, str):
        raise TypeError("PDP salt must be a str, got %r" % type(salt).__name__)
    if not salt:
        raise ValueError(
            "PDP salt is empty. Refusing to produce an unsalted digest - configure "
            "WAREHOUSE_MASK_SALT_<TENANT> or the ir.config_parameter 'pdp.mask_salt'."
        )
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(
            "PDP digest input must be a str or None, got %r. Serialise the value explicitly; "
            "there is no cross-language-safe implicit conversion." % type(value).__name__
        )
    if value == "":
        return None
    return hmac.new(
        salt.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()
