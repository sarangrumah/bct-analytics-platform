# -*- coding: utf-8 -*-
"""ISO alpha-3 country codes, as DJP's Coretax templates expect them.

Core ``res.country`` only stores alpha-2 (``code``), but every Coretax template
that names a country — Bupot Non-Resident's "Kode Negara", e-Faktur's
``KODE_NEGARA`` — wants alpha-3 (GBR, SGP, USA).

The seed in ``data/res_country_alpha3.csv`` is transcribed from the
``KODE_NEGARA`` sheet shipped inside the DJP e-Faktur template, so it is the
authority's own list rather than a generic ISO table. That list is *partial*:
it omits ~15 countries Odoo knows about (Cyprus, Fiji, Myanmar, Tanzania,
Samoa, …). Those are deliberately left blank rather than back-filled from ISO —
emitting a code DJP does not recognise would fail the import anyway, and a
blank surfaces the gap to the operator instead of hiding it.
"""

from __future__ import annotations

from odoo import fields, models


class ResCountry(models.Model):
    _inherit = "res.country"

    x_custom_code_alpha3 = fields.Char(
        string="Kode Negara (alpha-3)",
        size=3,
        help="ISO 3166-1 alpha-3 code as listed in the DJP Coretax templates. "
        "Blank means DJP's country master does not carry this country.",
    )
