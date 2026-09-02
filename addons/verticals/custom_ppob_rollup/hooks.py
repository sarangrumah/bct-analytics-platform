# -*- coding: utf-8 -*-
"""Post-init hook: flag the summary journal as excluded from financial reports.

The summary faktur posts real out_invoice GL (AR / Revenue / PPN) but revenue is
already booked gross per transaction, so the summary journal must be omitted from
Trial Balance / P&L / Balance Sheet. custom_accounting_reports provides the
generic ``account.journal.x_custom_report_excluded`` flag; we set it here.
"""

import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    journal = env.ref("custom_ppob_rollup.journal_ppob_summary", raise_if_not_found=False)
    if journal and "x_custom_report_excluded" in journal._fields:
        journal.x_custom_report_excluded = True
        _logger.info("custom_ppob_rollup: summary journal flagged report-excluded")
