# -*- coding: utf-8 -*-
import json

from markupsafe import Markup

from odoo import api, fields, models


class RetailImportLine(models.Model):
    _name = "retail.import.line"
    _description = "Retail Import Source Row"
    _order = "log_id, row_number"

    log_id = fields.Many2one("retail.import.log", required=True, ondelete="cascade", index=True)
    row_number = fields.Integer(index=True)
    aggregate_key = fields.Char(
        index=True, help="Grouping key used during aggregation (e.g. product_code, store|date|reg|transnum)."
    )
    raw_data_json = fields.Text(
        help="Full parsed row dict as JSON. May be purged after a retention period while keeping linkage metadata."
    )
    raw_data_html = fields.Html(
        string="Parsed Row",
        compute="_compute_raw_data_html",
        sanitize=False,
        help="raw_data_json rendered as a field/value table.",
    )

    @api.depends("raw_data_json")
    def _compute_raw_data_html(self):
        for line in self:
            line.raw_data_html = line._render_raw_data_table()

    def _render_raw_data_table(self):
        """Render raw_data_json as a two-column field/value HTML table."""
        self.ensure_one()
        if not self.raw_data_json:
            return False
        try:
            data = json.loads(self.raw_data_json)
        except (ValueError, TypeError):
            return False
        if not isinstance(data, dict):
            return False
        cell_style = "padding:2px 8px;"
        rows = Markup("")
        for key, val in data.items():
            rows += Markup('<tr><th style="{}white-space:nowrap;">{}</th><td style="{}">{}</td></tr>').format(
                cell_style, key, cell_style, "" if val is None else val
            )
        if not rows:
            return False
        return Markup(
            '<div style="overflow-x:auto;">'
            '<table class="table table-sm table-bordered" style="width:auto;">'
            "<thead><tr><th>Field</th><th>Value</th></tr></thead>"
            "<tbody>{}</tbody></table>"
            "</div>"
        ).format(rows)

    state = fields.Selection(
        [("ok", "OK"), ("skipped", "Skipped"), ("error", "Error")],
        default="ok",
        index=True,
    )
    error_message = fields.Text()
    target_model = fields.Char(help="Odoo model name of the record produced from this row.")
    target_res_id = fields.Integer(
        help="ID of the Odoo record produced from this row (or the aggregate it contributed to)."
    )
