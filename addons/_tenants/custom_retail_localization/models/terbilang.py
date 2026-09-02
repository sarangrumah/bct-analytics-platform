# -*- coding: utf-8 -*-
"""Indonesian amount-in-words ("terbilang").

Pure-Python so it is independent of the num2words / babel locale being loaded
in the QWeb sandbox (id_ID is usually not active, so ``amount_to_text`` would
fall back to English). Verified against the sample vouchers:

    3312112089 -> "tiga miliar tiga ratus dua belas juta seratus dua belas
                   ribu delapan puluh sembilan"
        270045 -> "dua ratus tujuh puluh ribu empat puluh lima"
"""

from __future__ import annotations

_SATUAN = [
    "",
    "satu",
    "dua",
    "tiga",
    "empat",
    "lima",
    "enam",
    "tujuh",
    "delapan",
    "sembilan",
    "sepuluh",
    "sebelas",
]


def _spell(x: int) -> str:
    if x < 12:
        return _SATUAN[x]
    if x < 20:
        return _spell(x - 10) + " belas"
    if x < 100:
        return _spell(x // 10) + " puluh" + ((" " + _spell(x % 10)) if x % 10 else "")
    if x < 200:
        return "seratus" + ((" " + _spell(x - 100)) if x - 100 else "")
    if x < 1000:
        return _spell(x // 100) + " ratus" + ((" " + _spell(x % 100)) if x % 100 else "")
    if x < 2000:
        return "seribu" + ((" " + _spell(x - 1000)) if x - 1000 else "")
    if x < 1_000_000:
        return _spell(x // 1000) + " ribu" + ((" " + _spell(x % 1000)) if x % 1000 else "")
    if x < 1_000_000_000:
        return _spell(x // 1_000_000) + " juta" + ((" " + _spell(x % 1_000_000)) if x % 1_000_000 else "")
    if x < 1_000_000_000_000:
        return _spell(x // 1_000_000_000) + " miliar" + ((" " + _spell(x % 1_000_000_000)) if x % 1_000_000_000 else "")
    return (
        _spell(x // 1_000_000_000_000)
        + " triliun"
        + ((" " + _spell(x % 1_000_000_000_000)) if x % 1_000_000_000_000 else "")
    )


def terbilang_id(amount, suffix: str = "Rupiah") -> str:
    """Return ``amount`` spelled out in Indonesian, Title Cased, e.g.
    ``"Dua Ratus Tujuh Puluh Ribu Empat Puluh Lima Rupiah"``.

    Fractional parts are rounded away (IDR vouchers are whole-rupiah).
    """
    n = int(round(abs(amount or 0.0)))
    words = "nol" if n == 0 else " ".join(_spell(n).split())
    text = words.title()
    return "%s %s" % (text, suffix) if suffix else text
