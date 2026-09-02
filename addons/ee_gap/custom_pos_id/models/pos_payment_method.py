# -*- coding: utf-8 -*-
"""QRIS-aware POS payment method.

Adds metadata for the Indonesian QRIS standard plus an
``action_generate_qris_payload`` helper that produces a Merchant-Presented Mode
(MPM) static QR payload. The payload string follows a simplified EMVCo TLV
layout sufficient for in-house mock acquirer testing; a real BCA / BRI /
Mandiri / QRIS gateway will return the canonical payload via their REST API.

The PNG bytes for the encoded QR are returned base64-encoded so the caller
can drop them straight into a ``Binary`` field or HTTP response.
"""

from __future__ import annotations

import base64
import io
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _tlv(tag: str, value: str) -> str:
    """Format one EMVCo Tag-Length-Value triplet."""
    return f"{tag}{len(value):02d}{value}"


def _crc16_ccitt(payload: str) -> str:
    """CRC-16/CCITT-FALSE used by EMVCo QR payloads."""
    crc = 0xFFFF
    for ch in payload.encode("utf-8"):
        crc ^= ch << 8
        for _bit in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return f"{crc:04X}"


# Provider stubs: a real driver would call the acquirer REST API
# (e.g. BCA Bizz, BRI Mocash, Mandiri Bizz). For now we surface a
# deterministic mock payload so QA + UI work end-to-end.
_PROVIDER_STUB_MIDS = {
    "bca": "ID1014BCABIZ12345678",
    "bri": "ID1014BRIMOCASH9999",
    "mandiri": "ID1014MANDIRIBIZ1111",
    "dana": "ID1014DANAMERCHANT001",
    "gopay": "ID1014GOPAYMERCHANT002",
    "ovo": "ID1014OVOMERCHANT003",
    "linkaja": "ID1014LINKAJAMERCH004",
    "shopeepay": "ID1014SHOPEEPAY005",
    "custom": "ID1014CUSTOMACQUIRER",
}


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    x_qris_provider = fields.Selection(
        [
            ("manual", "Manual / Offline"),
            ("bca", "BCA"),
            ("bri", "BRI"),
            ("mandiri", "Mandiri"),
            ("dana", "DANA"),
            ("gopay", "GoPay"),
            ("ovo", "OVO"),
            ("linkaja", "LinkAja"),
            ("shopeepay", "ShopeePay"),
            ("custom", "Custom"),
        ],
        string="QRIS Provider",
        default="manual",
        help="QRIS acquirer / e-wallet provider for this payment method.",
    )
    x_qris_merchant_id = fields.Char(
        string="QRIS Merchant ID",
        help="Merchant identifier registered with the QRIS provider.",
    )
    x_qris_merchant_name = fields.Char(
        string="QRIS Merchant Name",
        help="Display name used in field 59 of the QRIS payload.",
    )
    x_qris_merchant_city = fields.Char(
        string="QRIS Merchant City",
        default="JAKARTA",
        help="Display city used in field 60 of the QRIS payload.",
    )
    x_qris_static_qr = fields.Binary(
        string="QRIS Static QR",
        attachment=True,
        help="Static QRIS image used when dynamic QR generation is not available.",
    )
    x_qris_dynamic_supported = fields.Boolean(
        string="Dynamic QRIS Supported",
        default=False,
        help="Whether the provider supports per-transaction dynamic QR generation.",
    )
    x_qris_mid = fields.Char(
        string="Merchant ID",
        help="Acquirer-side Merchant ID (MID) used in QRIS payloads.",
    )

    # ---------- public API ----------

    def action_generate_qris_payload(self, transaction_amount=0.0):
        """Build a QRIS MPM payload + PNG QR image.

        :param transaction_amount: amount in IDR. When ``0`` (or for static
            providers that ``manual``) the payload omits the amount tag (54).
        :returns: ``{"payload": str, "qr_png_b64": str | None, "provider": str}``
        :raises UserError: if called on a manual provider.
        """
        self.ensure_one()
        if self.x_qris_provider == "manual":
            raise UserError(_("Payment method %s is configured as manual QRIS; no payload is generated.") % self.name)

        merchant_account = self.x_qris_mid or _PROVIDER_STUB_MIDS.get(self.x_qris_provider, "ID1014UNKNOWN")
        merchant_id = self.x_qris_merchant_id or "0000000000"
        merchant_name = (self.x_qris_merchant_name or self.name or "MERCHANT")[:25]
        merchant_city = (self.x_qris_merchant_city or "JAKARTA")[:15]

        parts: list[str] = []
        parts.append(_tlv("00", "01"))  # Payload Format Indicator
        # Static = 11, Dynamic = 12
        parts.append(_tlv("01", "12" if self.x_qris_dynamic_supported else "11"))
        # Merchant Account Info — tag 26 reserved for domestic schemes.
        merchant_info = _tlv("00", "ID.CO.QRIS.WWW") + _tlv("01", merchant_account) + _tlv("02", merchant_id)
        parts.append(_tlv("26", merchant_info))
        parts.append(_tlv("52", "5812"))  # MCC: convenience stores; placeholder
        parts.append(_tlv("53", "360"))  # Currency: IDR
        if transaction_amount and float(transaction_amount) > 0:
            # Field 54: amount (numeric, two decimals stripped if integer IDR)
            amount_str = f"{float(transaction_amount):.2f}".rstrip("0").rstrip(".") or "0"
            parts.append(_tlv("54", amount_str))
        parts.append(_tlv("58", "ID"))  # Country
        parts.append(_tlv("59", merchant_name))  # Merchant name
        parts.append(_tlv("60", merchant_city))  # Merchant city
        payload_wo_crc = "".join(parts) + "6304"
        crc = _crc16_ccitt(payload_wo_crc)
        payload = payload_wo_crc + crc

        qr_png_b64 = self._render_qr_png(payload)

        _logger.info(
            "QRIS payload generated method=%s provider=%s amount=%s",
            self.name,
            self.x_qris_provider,
            transaction_amount,
        )
        return {
            "payload": payload,
            "qr_png_b64": qr_png_b64,
            "provider": self.x_qris_provider,
        }

    # ---------- helpers ----------

    @staticmethod
    def _render_qr_png(payload: str) -> str | None:
        """Encode the payload string as a base64-PNG QR using stdlib qrcode.

        Returns ``None`` if the ``qrcode`` package is unavailable on the host
        (the payload string alone is still usable by callers that have their
        own renderer, e.g. the JS POS UI).
        """
        try:
            import qrcode  # type: ignore[import-not-found]
        except ImportError:
            _logger.warning("qrcode package not available; returning payload without PNG.")
            return None
        img = qrcode.make(payload)
        buf = io.BytesIO()
        # Pillow-backed images accept `format`; pure-PyPNG fallback does not,
        # but it always writes PNG when given a file-like with no extension.
        try:
            img.save(buf, format="PNG")
        except TypeError:
            img.save(buf)
        return base64.b64encode(buf.getvalue()).decode("ascii")
