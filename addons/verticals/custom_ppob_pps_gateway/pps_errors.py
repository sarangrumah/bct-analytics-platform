# -*- coding: utf-8 -*-
"""PPS status/error catalog: native condition -> (PPS Status code, message).

Status codes follow the vendor PPS contract: "0" success, "1" failed,
"9" pending (Sell/topup); check/inquiry additionally use "2" pending.
Wording mirrors the vendor PDF so a drop-in POS sees familiar messages.
"""

# code -> (pps_status, message)
PPS_ERRORS = {
    "BAD_SIGNATURE": ("1", "Signature tidak valid"),
    "BAD_FORMAT": ("1", "Format request salah"),
    "UNKNOWN_USER": ("1", "User tidak dikenali"),
    "IP_NOT_ALLOWED": ("1", "Akses ditolak dari IP ini"),
    "REPLAY": ("1", "Request sudah pernah diproses"),
    "INSUFFICIENT_DEPOSIT": ("1", "Deposit Anda tidak mencukupi untuk memenuhi penjualan"),
    "PRODUCT_NOT_FOUND": ("1", "Produk tidak ditemukan"),
    "NO_ROUTE": ("1", "Produk belum tersedia / sedang gangguan"),
    "STOCK_EMPTY": ("1", "Stok kosong, transaksi kami batalkan"),
    "ADAPTER_FAIL": ("1", "Transaksi gagal diproses"),
    "PENDING": ("9", "Transaksi sedang diproses"),
    "SUCCESS": ("0", "Transaksi berhasil"),
    "NOT_FOUND": ("1", "Transaksi tidak ditemukan"),
}

# Native failure error_code (from _mark_failed / AdapterResult) -> catalog key.
NATIVE_TO_PPS = {
    "MOCK_FAIL": "ADAPTER_FAIL",
    "ADAPTER_FAIL": "ADAPTER_FAIL",
    "ADAPTER_EXC": "ADAPTER_FAIL",
    "TIMEOUT": "PENDING",
    "STALE_CONFIRMED_FAIL": "ADAPTER_FAIL",
}


def resolve(code):
    """Return (pps_status, message) for a catalog key, defaulting to ADAPTER_FAIL."""
    return PPS_ERRORS.get(code) or PPS_ERRORS["ADAPTER_FAIL"]


def state_to_status(state):
    """Map a custom.ppob.transaction state to a PPS Status code."""
    if state == "success":
        return "0"
    if state in ("failed", "timeout", "refunded"):
        return "1"
    return "9"  # pending / inquiry_ok / in_progress
