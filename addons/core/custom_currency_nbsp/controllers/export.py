# -*- coding: utf-8 -*-
from odoo.addons.web.controllers.export import CSVExport

BOM = "﻿"


class CSVExportWithBom(CSVExport):
    """Make ``/web/export/csv`` self-describing.

    Core returns the CSV body as a str and lets werkzeug encode it as UTF-8,
    but writes no byte-order mark. Excel therefore falls back to the system
    codepage (cp1252 on an Indonesian Windows install) and renders every UTF-8
    multi-byte sequence as mojibake — which is where the ``Â`` next to Rupiah
    amounts came from. A leading BOM removes the guesswork; UTF-8-aware readers
    strip it silently.
    """

    def from_data(self, fields, columns_headers, rows):
        content = super().from_data(fields, columns_headers, rows)
        if content and not content.startswith(BOM):
            content = BOM + content
        return content
