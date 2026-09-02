# -*- coding: utf-8 -*-
"""ISOLATED MD5 signature verification for the PPS/EVShop H2H contract.

The vendor PPS contract mandates MD5 signatures, with a DIFFERENT formula per
endpoint (see the H2H API PPS spec). MD5 is cryptographically broken; it is used
here ONLY because the POS<->switcher contract requires it, and it is confined to
this single file so the platform's HMAC-SHA256 convention
(``custom_core.controllers.secure_endpoint``) is never diluted.

Do NOT import this module anywhere outside ``custom_ppob_pps_gateway``. The
signature alone is not the whole defense -- the controller also enforces an IP
allowlist, a replay/nonce guard and timestamp/notrx freshness.
"""

import hashlib
import hmac


def _md5(value):
    # usedforsecurity=False: MD5 is the vendor contract's wire format, not a
    # security control here. Freshness/replay/IP checks are enforced separately
    # by the controller (see module docstring).
    return hashlib.md5(  # nosemgrep
        (value or "").encode("utf-8"), usedforsecurity=False
    ).hexdigest()


# Per-endpoint signature formula: f(params, md5_password) -> expected hex digest.
# ``password`` passed in is the RAW shared secret; each formula hashes it as the
# spec dictates (always md5(password) as an inner term).
SIG_FORMULAS = {
    # MD5(mdn + produk + notrx + md5(password))
    "sell": lambda p, pw: _md5((p.get("mdn") or "") + (p.get("produk") or "") + (p.get("notrx") or "") + _md5(pw)),
    # MD5(notrx + md5(password))
    "statustrx": lambda p, pw: _md5((p.get("notrx") or "") + _md5(pw)),
    "statustrxdeposit": lambda p, pw: _md5((p.get("notrx") or "") + _md5(pw)),
    # MD5(notrx + user + product + md5(password) + customer_no)
    "checknocustomer": lambda p, pw: _md5(
        (p.get("notrx") or "")
        + (p.get("user") or "")
        + (p.get("product") or "")
        + _md5(pw)
        + (p.get("customer_no") or "")
    ),
    # MD5(customerNumber + user + md5(password))
    "inquiry_pln": lambda p, pw: _md5((p.get("customerNumber") or "") + (p.get("user") or "") + _md5(pw)),
    # MD5(timestamp + md5(password))
    "game_list": lambda p, pw: _md5((p.get("timestamp") or "") + _md5(pw)),
    # MD5(md5(password) + username + produk + noTrx)
    "direct_topup": lambda p, pw: _md5(
        _md5(pw) + (p.get("user") or "") + (p.get("product") or "") + (p.get("notrx") or "")
    ),
}


def verify(endpoint, params, password):
    """Constant-time compare of the per-endpoint MD5 signature.

    Returns True only if a formula exists for ``endpoint``, a password is set,
    and the supplied ``signature`` matches.
    """
    formula = SIG_FORMULAS.get(endpoint)
    if not formula or not password:
        return False
    expected = formula(params, password)
    return hmac.compare_digest(expected, params.get("signature") or "")
