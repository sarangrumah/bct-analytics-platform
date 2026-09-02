# -*- coding: utf-8 -*-
"""Add a "by Show" variant to the shared Profit & Loss wizard (the tenant only).

Mirrors the built-in "by Branch" variant: same filters, but the result is
pivoted into one amount column per Show Date. The show columns are bounded to
the report period via the context so the statement doesn't sprout one column
per show across all time. Screen + XLSX only (dynamic columns, no PDF).
"""

from odoo import models


class ProfitLossWizardShow(models.TransientModel):
    _inherit = "custom.report.profit.loss.wizard"

    def _show_context_extra(self):
        self.ensure_one()
        return {
            "pl_show_from": self.date_from,
            "pl_show_to": self.date_to,
            "pl_posted_only": self.posted_only,
        }

    def action_view_by_show(self):
        self.ensure_one()
        title = self.env["custom.report.profit.loss.show"]._report_title
        return {
            "type": "ir.actions.client",
            "tag": "custom_report_table",
            "name": title,
            "params": {
                "report_code": "profit_loss_show",
                "options": self._report_options(),
                "context_extra": {
                    "pl_show_from": self.date_from.isoformat(),
                    "pl_show_to": self.date_to.isoformat(),
                    "pl_posted_only": self.posted_only,
                },
                "title": title,
            },
        }

    def action_export_xlsx_by_show(self):
        self.ensure_one()
        options = {
            **self._build_filters(),
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
        }
        filename = "Profit_Loss_by_Show_%s_%s.xlsx" % (self.date_from, self.date_to)
        return (
            self.env["custom.report.profit.loss.show"]
            .with_context(**self._show_context_extra())
            ._xlsx_action(options, filename)
        )
