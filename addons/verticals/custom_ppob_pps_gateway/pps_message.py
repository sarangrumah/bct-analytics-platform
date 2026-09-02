# -*- coding: utf-8 -*-
"""Reconstruct the PPS human-readable Message strings from a transaction.

Wording follows the vendor PPS PDF so a drop-in POS displays familiar text,
e.g. "Pengisian Voucher sebesar 15000 ke nomor 0811... berhasil dengan no ref
<...>". Kept in one place so the phrasing can be tuned without touching logic.
"""

from .pps_errors import PPS_ERRORS, NATIVE_TO_PPS


def _amount(txn):
    return int(round(txn.sell_price or txn.product_id.denom or 0.0))


def sale_message(txn):
    """Message for a Sell/direct-topup transaction based on its state."""
    if txn.state == "success":
        ref = txn.serial_token or txn.provider_ref or ""
        return ("Pengisian %(prod)s sebesar %(amt)s ke nomor %(mdn)s berhasil dengan no ref <%(ref)s>") % {
            "prod": txn.product_id.name or txn.product_id.code or "produk",
            "amt": _amount(txn),
            "mdn": txn.msisdn or "",
            "ref": ref,
        }
    if txn.state in ("failed", "timeout", "refunded"):
        key = NATIVE_TO_PPS.get(txn.error_code or "", "ADAPTER_FAIL")
        base = PPS_ERRORS.get(key, PPS_ERRORS["ADAPTER_FAIL"])[1]
        return ("Pengisian %(prod)s sebesar %(amt)s ke nomor %(mdn)s GAGAL. %(msg)s") % {
            "prod": txn.product_id.name or txn.product_id.code or "produk",
            "amt": _amount(txn),
            "mdn": txn.msisdn or "",
            "msg": txn.error_message or base,
        }
    return PPS_ERRORS["PENDING"][1]


def with_deposit(message, balance):
    """Append the mitra deposit to a status message (StatusTrxWithDeposit)."""
    return "%s Deposit Anda saat ini adalah %s." % (message or "", "{:,.2f}".format(balance or 0.0))
