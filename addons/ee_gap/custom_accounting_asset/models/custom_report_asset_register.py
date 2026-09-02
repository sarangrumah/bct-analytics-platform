# -*- coding: utf-8 -*-
"""Fixed Asset Register & Depreciation Schedule (Daftar Aktiva Tetap).

Reuses the ``custom.report.engine`` XLSX infrastructure from
``custom_accounting_reports``: one row per asset with the acquisition
value, accumulated depreciation at the start of the year, the monthly
depreciation amounts (Jan–Dec of the selected year, taken from the
scheduled depreciation lines), year-to-date depreciation, accumulated
depreciation at year end and the resulting book value.
"""

from odoo import models


MONTH_LABELS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


class CustomReportAssetRegister(models.AbstractModel):
    _name = "custom.report.asset.register"
    _inherit = "custom.report.engine"
    _description = "Fixed Asset Register & Depreciation Schedule"

    _report_code = "asset_register"
    _report_title = "Daftar Aktiva Tetap & Penyusutan"

    def _xlsx_columns(self):
        cols = [
            {"header": "No", "field": "no", "kind": "text", "width": 5},
            {"header": "Code", "field": "code", "kind": "text", "width": 14},
            {"header": "Asset Name", "field": "name", "kind": "text", "width": 34},
            {"header": "Group", "field": "group", "kind": "text", "width": 18},
            {"header": "Acq. Date", "field": "acq_date", "kind": "date", "width": 12},
            {"header": "Life (mo)", "field": "life", "kind": "number", "width": 9},
            {"header": "Acquisition Value", "field": "acq_value", "kind": "number", "width": 18},
            {"header": "Accum. Opening", "field": "opening", "kind": "number", "width": 16},
        ]
        for i, label in enumerate(MONTH_LABELS):
            cols.append({"header": label, "field": "m%d" % i, "kind": "number", "width": 13})
        cols += [
            {"header": "YTD", "field": "ytd", "kind": "number", "width": 15},
            {"header": "Accum. End", "field": "accum_end", "kind": "number", "width": 16},
            {"header": "Book Value", "field": "book", "kind": "number", "width": 16},
        ]
        return cols

    # Uses the engine's generic flat ``_xlsx_body`` (rows + grand_total).

    def _build_lines(self, filters):
        from datetime import date as date_cls

        year = filters.get("year") or filters["date_to"].year
        jan1 = date_cls(year, 1, 1)

        Asset = self.env["custom.fixed.asset"]
        domain = [("company_id", "in", filters["company_ids"])]
        if filters.get("group_ids"):
            domain.append(("group_id", "in", filters["group_ids"]))
        if filters.get("location_ids"):
            domain.append(("location_id", "in", filters["location_ids"]))
        if filters.get("asset_states"):
            domain.append(("state", "in", filters["asset_states"]))
        assets = Asset.search(domain, order="code, id")

        lines = []
        totals = {
            "acq_value": 0.0,
            "opening": 0.0,
            "ytd": 0.0,
            "accum_end": 0.0,
            "book": 0.0,
            **{"m%d" % i: 0.0 for i in range(12)},
        }
        for index, asset in enumerate(assets, start=1):
            months = [0.0] * 12
            opening = 0.0
            for dep in asset.depreciation_line_ids:
                if not dep.date:
                    continue
                if dep.date < jan1:
                    opening += dep.amount
                elif dep.date.year == year:
                    months[dep.date.month - 1] += dep.amount
            ytd = sum(months)
            accum_end = opening + ytd
            book = (asset.acquisition_value or 0.0) - accum_end

            row = {
                "no": str(index),
                "code": asset.code or "",
                "name": asset.name or "",
                "group": asset.group_id.name or "",
                "acq_date": asset.acquisition_date,
                "life": asset.useful_life_months or 0,
                "acq_value": asset.acquisition_value or 0.0,
                "opening": opening,
                "ytd": ytd,
                "accum_end": accum_end,
                "book": book,
            }
            for i in range(12):
                row["m%d" % i] = months[i]
            lines.append(row)

            totals["acq_value"] += row["acq_value"]
            totals["opening"] += opening
            totals["ytd"] += ytd
            totals["accum_end"] += accum_end
            totals["book"] += book
            for i in range(12):
                totals["m%d" % i] += months[i]

        totals.update({"type": "grand_total", "name": "TOTAL"})
        lines.append(totals)
        return lines


class ReportAssetRegister(models.AbstractModel):
    _name = "report.custom_accounting_asset.report_asset_register"
    _description = "Asset Register PDF Renderer"

    def _get_report_values(self, docids, data=None):
        data = data or {}
        report = self.env["custom.report.asset.register"]
        ctx = report._compute(data.get("options") or data.get("filters"))
        return {
            "doc_ids": docids,
            "doc_model": data.get("doc_model", ""),
            "docs": [],
            **ctx,
        }
