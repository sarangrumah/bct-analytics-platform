# -*- coding: utf-8 -*-
from odoo import fields, models


class CustomBarcodeScanLine(models.Model):
    _inherit = "custom.barcode.scan.line"

    supplier_batch_ref = fields.Char(
        string="Supplier Batch",
        help="Supplier batch reference for this scan; copied onto the lot "
        "when the session is applied. Left empty, the GS1 lot (AI 10) is "
        "used instead.",
    )
