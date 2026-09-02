# -*- coding: utf-8 -*-
"""Barcode image URL helper.

Barcode images are rendered by the core web route
``/report/barcode/<Type>/<value>`` (``odoo.addons.web.controllers.report``).
Supported symbologies include ``Code128``, ``QR`` and ``datamatrix``; the core
controller silently falls back to ``Code128`` for anything reportlab cannot
draw, so an unknown symbology never breaks a print job.

The value is URL-quoted with ``safe=""`` because product / package references
routinely contain ``/`` (e.g. ``WH/OUT/00007``) and the route uses a
``<path:value>`` converter that would otherwise swallow the slashes.
"""

from __future__ import annotations

import logging
from base64 import b64encode
from urllib.parse import quote

_logger = logging.getLogger(__name__)

#: Symbologies offered to the user in the label wizard.
WMS_BARCODE_TYPES = ("Code128", "QR", "datamatrix")


def wms_barcode_url(
    value: str,
    barcode_type: str = "Code128",
    width: int = 600,
    height: int = 100,
    humanreadable: bool = False,
) -> str:
    """Return the ``<img src>`` URL rendering ``value`` as a barcode image.

    :param value: the human readable payload (may be empty → empty string).
    :param barcode_type: ``Code128`` / ``QR`` / ``datamatrix`` / any reportlab type.
    :param width: pixel width requested from the core controller.
    :param height: pixel height requested from the core controller.
    :param humanreadable: render the value underneath the bars.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    return "/report/barcode/%s/%s?width=%s&height=%s&humanreadable=%s" % (
        quote(str(barcode_type or "Code128"), safe=""),
        quote(text, safe=""),
        int(width),
        int(height),
        1 if humanreadable else 0,
    )


def wms_barcode_png(
    env,
    value: str,
    barcode_type: str = "Code128",
    width: int = 600,
    height: int = 100,
    humanreadable: bool = False,
) -> bytes:
    """Render ``value`` to raw PNG bytes, in-process.

    Same symbologies as :func:`wms_barcode_url`, but rendered through
    ``ir.actions.report.barcode()`` instead of the HTTP route — so a PDF that
    embeds the result never makes wkhtmltopdf call back into Odoo, and the
    same bytes can be dropped straight into an XLSX sheet.

    Returns ``b""`` for an empty payload or a value reportlab refuses (a
    13-char string handed to ``EAN13``, for instance) — a bad barcode must
    never take a print job down.
    """
    text = str(value or "").strip()
    if not text:
        return b""
    try:
        return env["ir.actions.report"].barcode(
            barcode_type or "Code128",
            text,
            width=int(width),
            height=int(height),
            humanreadable=1 if humanreadable else 0,
        )
    except Exception:  # noqa: BLE001 - unrenderable payload, not a failure of the document
        _logger.warning("WMS: cannot render %s barcode for %r", barcode_type, text)
        return b""


def wms_barcode_data_uri(
    env,
    value: str,
    barcode_type: str = "Code128",
    width: int = 600,
    height: int = 100,
    humanreadable: bool = False,
) -> str:
    """:func:`wms_barcode_png` wrapped as a ``data:image/png;base64,`` URI."""
    png = wms_barcode_png(env, value, barcode_type, width, height, humanreadable)
    if not png:
        return ""
    return "data:image/png;base64,%s" % b64encode(png).decode("ascii")
