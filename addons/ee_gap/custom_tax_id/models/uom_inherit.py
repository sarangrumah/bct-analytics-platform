# -*- coding: utf-8 -*-
"""Coretax unit-of-measure codes for the e-Faktur SATUAN column.

Transcribed from the ``SATUAN_BARANG_JASA`` sheet of the DJP e-Faktur template.
DJP's list is short (33 entries) and does not line up with Odoo's UoM tree, so
the mapping is an explicit per-UoM choice rather than a name match.
"""

from __future__ import annotations

from odoo import fields, models

# code -> label, verbatim from SATUAN_BARANG_JASA.
CORETAX_UOM_SELECTION = [
    ("UM.0001", "UM.0001 - Metrik Ton"),
    ("UM.0002", "UM.0002 - Wet Ton"),
    ("UM.0003", "UM.0003 - Kilogram"),
    ("UM.0004", "UM.0004 - Gram"),
    ("UM.0005", "UM.0005 - Karat"),
    ("UM.0006", "UM.0006 - Kiloliter"),
    ("UM.0007", "UM.0007 - Liter"),
    ("UM.0008", "UM.0008 - Barrel"),
    ("UM.0009", "UM.0009 - MMBTU"),
    ("UM.0010", "UM.0010 - Ampere"),
    ("UM.0011", "UM.0011 - Sentimeter Kubik"),
    ("UM.0012", "UM.0012 - Meter Persegi"),
    ("UM.0013", "UM.0013 - Meter"),
    ("UM.0014", "UM.0014 - Inches"),
    ("UM.0015", "UM.0015 - Sentimeter"),
    ("UM.0016", "UM.0016 - Yard"),
    ("UM.0017", "UM.0017 - Lusin"),
    ("UM.0018", "UM.0018 - Unit"),
    ("UM.0019", "UM.0019 - Set"),
    ("UM.0020", "UM.0020 - Lembar"),
    ("UM.0021", "UM.0021 - Piece"),
    ("UM.0022", "UM.0022 - Boks"),
    ("UM.0023", "UM.0023 - Tahun"),
    ("UM.0024", "UM.0024 - Bulan"),
    ("UM.0025", "UM.0025 - Minggu"),
    ("UM.0026", "UM.0026 - Hari"),
    ("UM.0027", "UM.0027 - Jam"),
    ("UM.0028", "UM.0028 - Menit"),
    ("UM.0029", "UM.0029 - Persen"),
    ("UM.0030", "UM.0030 - Kegiatan"),
    ("UM.0031", "UM.0031 - Laporan"),
    ("UM.0032", "UM.0032 - Bahan"),
    ("UM.0033", "UM.0033 - Other"),
]

# What the e-Faktur exporter emits for a UoM nobody has mapped. Retail goods are
# sold per unit and every line of the client's own sample files carries UM.0018,
# so this is the least-surprising fallback — but it is a guess, and mapping the
# UoM explicitly is always better.
CORETAX_UOM_FALLBACK = "UM.0018"


class UomUom(models.Model):
    _inherit = "uom.uom"

    x_custom_coretax_code = fields.Selection(
        CORETAX_UOM_SELECTION,
        string="Kode Satuan Coretax",
        help="Satuan code emitted in the e-Faktur SATUAN column. Left empty, "
        "the exporter falls back to UM.0018 (Unit).",
    )
