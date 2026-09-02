# -*- coding: utf-8 -*-
"""Wizard: pick perimeter + date range + report type, render PDF / Excel."""

from __future__ import annotations

from datetime import date

from odoo import _, fields, models
from odoo.exceptions import UserError


class ConsolidationReportWizard(models.TransientModel):
    _name = "account.consolidation.report.wizard"
    _description = "Consolidation Report Wizard"

    config_id = fields.Many2one("account.consolidation.config", required=True)
    report_type = fields.Selection(
        [
            ("trial_balance", "Trial Balance"),
            ("profit_loss", "Profit &amp; Loss"),
            ("balance_sheet", "Balance Sheet"),
        ],
        required=True,
        default="trial_balance",
    )
    date_from = fields.Date(required=True, default=lambda self: date.today().replace(month=1, day=1))
    date_to = fields.Date(required=True, default=fields.Date.context_today)
    output_format = fields.Selection(
        [("pdf", "PDF"), ("html", "HTML preview")],
        default="pdf",
        required=True,
    )

    def action_render(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_("'Date To' must be on or after 'Date From'."))

        if self.report_type == "trial_balance":
            data = self.config_id.build_trial_balance(self.date_from, self.date_to)
            report_xmlid = "custom_accounting_full.action_consol_trial_balance_pdf"
        elif self.report_type == "profit_loss":
            data = self._build_pl()
            report_xmlid = "custom_accounting_full.action_consol_pl_pdf"
        else:
            data = self._build_bs()
            report_xmlid = "custom_accounting_full.action_consol_bs_pdf"

        if self.output_format == "html":
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Consolidation rendered"),
                    "message": _("%s accounts processed.") % len(data["accounts"]),
                    "type": "success",
                    "sticky": False,
                },
            }
        return self.env.ref(report_xmlid).report_action(self, data={"data": data})

    def _build_pl(self):
        """Slice trial balance to income + expense rows only."""
        full = self.config_id.build_trial_balance(self.date_from, self.date_to)
        income = ("income", "income_other")
        expense = ("expense", "expense_direct_cost", "expense_depreciation")
        full["accounts"] = [a for a in full["accounts"] if a["account_type"] in income or a["account_type"] in expense]
        full["section"] = "profit_loss"
        return full

    def _build_bs(self):
        full = self.config_id.build_trial_balance(self.date_from, self.date_to)
        asset = (
            "asset_receivable",
            "asset_cash",
            "asset_current",
            "asset_non_current",
            "asset_prepayments",
            "asset_fixed",
        )
        liab = ("liability_payable", "liability_current", "liability_non_current")
        equity = ("equity", "equity_unaffected")
        keep = asset + liab + equity
        full["accounts"] = [a for a in full["accounts"] if a["account_type"] in keep]
        full["section"] = "balance_sheet"
        return full
