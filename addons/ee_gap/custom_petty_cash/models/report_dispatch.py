# -*- coding: utf-8 -*-
"""Register the petty-cash reports with the shared report dispatcher.

``custom.report.engine`` resolves a ``report_code`` → model via the
module-level ``REPORT_MODEL_MAP`` in ``custom_accounting_reports``. Both the
on-screen table (``get_report_table``) and the QWeb-PDF path
(``_get_report_values``) read that dict, so adding our codes here is enough to
light up every surface — no per-report glue needed.
"""

from __future__ import annotations

from odoo.addons.custom_accounting_reports.models.custom_report_dispatch import REPORT_MODEL_MAP

REPORT_MODEL_MAP.update(
    {
        "petty_cash_outstanding": "petty.cash.report.outstanding",
        "petty_cash_aging": "petty.cash.report.aging",
    }
)
