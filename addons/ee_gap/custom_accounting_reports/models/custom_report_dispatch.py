# -*- coding: utf-8 -*-
"""Glue between Odoo's ``ir.actions.report`` dispatcher and each
concrete ``custom.report.*`` AbstractModel.

We register a single ``report.custom_accounting_reports.report_dispatch``
service. Wizards push the desired ``report_code`` + filters via the
``data`` payload; the dispatcher looks up the right AbstractModel,
builds its context, then hands off to a router QWeb template that
in turn includes the per-report layout.
"""

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .custom_report_general_ledger import GL_OPTIONAL_COLUMNS

_logger = logging.getLogger(__name__)


REPORT_MODEL_MAP = {
    "general_ledger": "custom.report.general.ledger",
    "trial_balance": "custom.report.trial.balance",
    "balance_sheet": "custom.report.balance.sheet",
    "profit_loss": "custom.report.profit.loss",
    "profit_loss_branch": "custom.report.profit.loss.branch",
    "cash_flow": "custom.report.cash.flow",
    "aged_receivable": "custom.report.aged.receivable",
    "aged_payable": "custom.report.aged.payable",
    "partner_ledger": "custom.report.partner.ledger",
    "payable_card": "custom.report.payable.card",
    "receivable_card": "custom.report.receivable.card",
    "advance": "custom.report.advance",
    "sales": "custom.report.sales",
    "sales_detail": "custom.report.sales.detail",
    "purchase": "custom.report.purchase",
    "credit_limit": "custom.report.credit.limit",
    "gl_open_items": "custom.report.gl.open.items",
    "bill_payment": "custom.report.bill.payment",
    "tax": "custom.report.tax",
    "faktur_pajak": "custom.report.faktur.pajak",
    "bupot": "custom.report.bupot",
    "spt_ppn": "custom.report.spt.ppn",
    "pph_withholding": "custom.report.pph.withholding",
    "nsfp_monitoring": "custom.report.nsfp.monitoring",
    "npwp_quality": "custom.report.npwp.quality",
    "dpp_nilai_lain": "custom.report.dpp.nilai.lain",
    "ppn_masukan_import": "custom.report.ppn.masukan.import",
    "faktur_pengganti": "custom.report.faktur.pengganti",
    "ekualisasi_omzet": "custom.report.ekualisasi.omzet",
    "pph_equalisasi": "custom.report.pph.equalisasi",
    "coretax_submission": "custom.report.coretax.submission",
    "pajakku_usage": "custom.report.pajakku.usage",
    "pph_reconciliation": "custom.report.pph.reconciliation",
    "pph25": "custom.report.pph25",
    "tax_audit": "custom.report.tax.audit",
    "day_book": "custom.report.day.book",
    "cash_book": "custom.report.cash.book",
    "bank_book": "custom.report.bank.book",
    "journal_audit": "custom.report.journal.audit",
    "financial": "custom.report.financial.renderer",
}


class CustomReportDispatch(models.AbstractModel):
    _name = "report.custom_accounting_reports.report_dispatch"
    _description = "Custom Financial Report Dispatcher"

    @api.model
    def _resolve_model_name(self, report_code):
        """Map ``report_code`` to its AbstractModel name.

        An unregistered code falls back to the Trial Balance so a stale wizard
        never hard-errors — but it is logged, because that fallback is silent
        from the user's side: they ask for e.g. the Purchase register and get a
        Trial Balance back with no clue why. Three reports (purchase,
        credit_limit, ppn_masukan_import) shipped unregistered and reached
        users exactly this way, so keep the warning when adding new reports.
        """
        model_name = REPORT_MODEL_MAP.get(report_code)
        if not model_name:
            _logger.warning(
                "Report code %r is not in REPORT_MODEL_MAP — falling back to "
                "the Trial Balance. Register the report's code to fix this.",
                report_code,
            )
            model_name = "custom.report.trial.balance"
        return model_name

    def _get_report_values(self, docids, data=None):
        data = data or {}
        report_code = data.get("report_code") or "trial_balance"
        report = self.env[self._resolve_model_name(report_code)]
        ctx = report._compute(data.get("options") or data.get("filters"))
        return {
            "doc_ids": docids,
            "doc_model": data.get("doc_model", ""),
            "docs": (self.env[data["doc_model"]].browse(docids) if data.get("doc_model") else []),
            "report_code": report_code,
            **ctx,
        }

    # ------------------------------------------------------------------
    # On-screen table / export entry points (called by the OWL client)
    # ------------------------------------------------------------------
    def _report_model(self, report_code):
        return self.env[self._resolve_model_name(report_code)]

    @api.model
    def get_report_table(self, report_code, options=None, context_extra=None):
        """Single JSON entry point for the OWL table client action.

        Resolves ``report_code`` via :data:`REPORT_MODEL_MAP` and returns
        the report's :py:meth:`~custom.report.engine.get_report_table`
        payload (columns + display rows)."""
        return self._report_model(report_code).get_report_table(options, context_extra)

    @api.model
    def _opening_period(self, date_from, company):
        """The stretch of ``date_from``'s fiscal year that precedes it —
        i.e. the movements this year that built up an opening balance.

        Note this is NOT the full span the Trial Balance sums for its opening
        column (which reaches back to the first entry ever), so the drill-down
        reconciles with the opening figure only when the account carries no
        balance from earlier years. Finance asked for the this-year view.
        """
        fy = company.compute_fiscalyear_dates(date_from)
        return fy["date_from"], date_from - timedelta(days=1)

    @api.model
    def get_drilldown_action(self, options=None, account_id=None, scope="period"):
        """Open the General Ledger for one account, keeping the calling
        report's company/journal/posted filters.

        Used by the OWL table when a user clicks an account row (e.g. on the
        Trial Balance): same window, GL flat layout, ``account_ids`` pinned to
        the clicked account.

        ``scope`` picks the period: ``"period"`` (default) reuses the calling
        report's dates; ``"opening"`` — fired by a column flagged
        ``drilldown: "opening"`` — rewinds to :py:meth:`_opening_period`.
        """
        engine = self.env["custom.report.general.ledger"]
        if not engine._drilldown_enabled():
            raise UserError(_("General Ledger drill-down is not enabled on this database."))
        account = self.env["account.account"].browse(account_id).exists()
        if not account:
            raise UserError(_("This row is not linked to an account."))
        gl_options = {
            key: value
            for key, value in (options or {}).items()
            if key in ("date_from", "date_to", "company_ids", "journal_ids", "posted_only")
        }
        gl_options["account_ids"] = account.ids
        title = _("General Ledger — %(code)s %(name)s") % {
            "code": engine._account_code(account),
            "name": account.name or "",
        }
        if scope == "opening":
            company = self.env["res.company"].browse((gl_options.get("company_ids") or self.env.companies.ids)[:1])
            date_from = fields.Date.to_date(gl_options.get("date_from")) or fields.Date.context_today(self)
            opening_from, opening_to = self._opening_period(date_from, company)
            if opening_to < opening_from:
                raise UserError(
                    _("The period starts on the first day of its fiscal year — there is no opening movement to show.")
                )
            gl_options["date_from"] = opening_from.isoformat()
            gl_options["date_to"] = opening_to.isoformat()
            title = _("%(title)s — Opening") % {"title": title}
        return {
            "type": "ir.actions.client",
            "tag": "custom_report_table",
            "name": title,
            "params": {
                "report_code": "general_ledger",
                "options": gl_options,
                "context_extra": {
                    "gl_layout": "flat",
                    "gl_columns": list(GL_OPTIONAL_COLUMNS),
                },
                "title": title,
            },
        }

    @api.model
    def run_xlsx(self, report_code, options=None, context_extra=None, filename=None):
        """Build the Excel download for ``report_code`` — mirrors the
        wizard's ``action_export_xlsx`` so the client's Export button is
        wizard-independent."""
        report = self._report_model(report_code).with_context(**(context_extra or {}))
        filename = filename or ("%s.xlsx" % report_code)
        return report._xlsx_action(options, filename)

    @api.model
    def run_pdf(self, report_code, options=None):
        """Build the QWeb-PDF download for ``report_code`` — mirrors the
        wizard's ``action_print``."""
        data = {"report_code": report_code, "options": options or {}}
        return self.env.ref("custom_accounting_reports.action_report_custom_financial").report_action([], data=data)
