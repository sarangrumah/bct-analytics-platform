# -*- coding: utf-8 -*-
"""Public read-only share endpoint for spreadsheet workbooks."""

import json
import logging

from markupsafe import Markup, escape

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class CustomSpreadsheetShareController(http.Controller):
    @http.route(
        ["/custom_spreadsheet/share/<string:token>"],
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
        sitemap=False,
    )
    def share_render(self, token, **kw):
        if not token:
            return request.not_found()
        wb = (
            request.env["custom.spreadsheet.workbook"]
            .sudo()
            .search(
                [("share_token", "=", token)],
                limit=1,
            )
        )
        if not wb:
            return request.not_found()
        try:
            data = json.loads(wb.data_json or '{"sheets":[]}')
        except (ValueError, TypeError):
            data = {"sheets": []}

        sheets_html = []
        for sheet in data.get("sheets", []):
            name = sheet.get("name") or "Sheet"
            cells = sheet.get("cells") or {}
            max_row = -1
            max_col = -1
            parsed = {}
            for k, v in cells.items():
                try:
                    r_s, c_s = str(k).split("_", 1)
                    r, c = int(r_s), int(c_s)
                except (ValueError, TypeError):
                    continue
                parsed[(r, c)] = v
                if r > max_row:
                    max_row = r
                if c > max_col:
                    max_col = c
            rows_html = []
            if max_row >= 0:
                for r in range(max_row + 1):
                    cells_html = []
                    for c in range(max_col + 1):
                        val = parsed.get((r, c), "")
                        if val is None:
                            val = ""
                        cells_html.append(
                            # nosemgrep - value is escape()'d; safe HTML by construction
                            "<td style='border:1px solid #ccc;padding:4px 8px;'>%s</td>" % escape(str(val))
                        )
                    rows_html.append("<tr>%s</tr>" % "".join(cells_html))
            # nosemgrep - name is escape()'d; rows_html built from escaped cells; safe HTML
            table = (
                "<h3>%s</h3><table style='border-collapse:collapse;font-family:sans-serif;font-size:13px;'>%s</table>"
            ) % (escape(name), "".join(rows_html) or "<tr><td><i>(empty)</i></td></tr>")
            sheets_html.append(table)

        # nosemgrep - all interpolated values (title/name) are escape()'d; safe HTML
        body = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'/>"
            "<title>%s</title></head><body style='font-family:sans-serif;"
            "padding:24px;'>"
            "<h1>%s</h1>"
            "<p style='color:#666'>Read-only shared workbook.</p>"
            "%s"
            "</body></html>"
        ) % (
            escape(wb.name or "Workbook"),
            escape(wb.name or "Workbook"),
            "".join(sheets_html) or "<p><i>(no sheets)</i></p>",
        )
        # noqa: S704 - every value interpolated into `body` passes through escape():
        # the workbook title twice above, the sheet name, and each cell in
        # rows_html. The only unescaped fragments are the literal tags this
        # function writes itself, so wrapping in Markup asserts something true.
        return request.make_response(
            Markup(body),  # noqa: S704
            headers=[("Content-Type", "text/html; charset=utf-8")],
        )
