# -*- coding: utf-8 -*-
"""Profit & Loss split per Show (the drone-show rental).

Same rows as the ordinary Profit & Loss — the account-group hierarchy and the
running-profit milestones are inherited verbatim from the *branch* variant in
``custom_accounting_reports`` — but one amount column per **Show Date** instead
of per Operating Unit.

A "Show" is identified by ``account.move.x_custom_show_date`` (this module): a
Date carried on both customer invoices (auto-set from the sale order's Show
Date) and — once operators tag them — vendor bills / refunds. Journal items
whose move carries no Show Date land in the **Unassigned** column (computed as a
residual), which is how Finance sees the attribution gap. Tagging a vendor bill
with its Show Date moves its cost out of Unassigned into that show's column.

Implementation note: this reuses the branch pivot machinery unchanged. The
branch code keys per-column amounts on an ``analytic_id``; here we slot the Show
Date (a ``date``) into that same position, so ``_build_lines`` / ``_account_row``
/ the flat XLSX + screen renderers all work as-is. Only the column list and the
per-column SQL aggregation are overridden. Like the branch report this is
**screen + XLSX only** (dynamic columns don't fit a static PDF).

TENANT-SCOPED: lives here (not in the shared engine) because it depends on the
the tenant ``x_custom_show_date`` column, which only exists where this module is
installed. Columns are bounded to a show-date window read from the context
(``pl_show_from`` / ``pl_show_to``, set by the P&L wizard to the report period)
so the statement doesn't explode to one column per show across all time.
"""

from odoo import models


class CustomReportProfitLossShow(models.AbstractModel):
    _name = "custom.report.profit.loss.show"
    _inherit = "custom.report.profit.loss.branch"
    _description = "Custom Profit & Loss per Show"

    _report_code = "profit_loss_show"
    _report_title = "Profit & Loss per Show"

    # ------------------------------------------------------------------
    # Show columns (Unassigned residual first, then one per Show Date).
    # No-arg to match the parent contract; period/company scope come from
    # the context so the same column set drives headers (``_xlsx_columns``)
    # and data (``_build_lines``).
    # ------------------------------------------------------------------
    def _branch_columns(self):
        columns = [(self.HQ_KEY, "Unassigned", None)]

        ctx = self.env.context
        show_from = ctx.get("pl_show_from")
        show_to = ctx.get("pl_show_to")
        params = [tuple(self.env.companies.ids) or (0,)]
        sql = """
            SELECT DISTINCT am.x_custom_show_date AS show_date
              FROM account_move_line aml
              JOIN account_move am ON am.id = aml.move_id
             WHERE aml.company_id IN %s
               AND aml.account_id IS NOT NULL
               AND am.x_custom_show_date IS NOT NULL
        """
        if ctx.get("pl_posted_only", True):
            sql += " AND aml.parent_state = %s"
            params.append("posted")
        else:
            sql += " AND aml.parent_state IN %s"
            params.append(("draft", "posted"))
        if show_from:
            sql += " AND am.x_custom_show_date >= %s"
            params.append(show_from)
        if show_to:
            sql += " AND am.x_custom_show_date <= %s"
            params.append(show_to)
        sql += " ORDER BY show_date"

        self.env.flush_all()
        self.env.cr.execute(sql, tuple(params))
        for row in self.env.cr.dictfetchall():
            show_date = row["show_date"]
            columns.append(("show_%s" % show_date.isoformat(), show_date.strftime("%d-%b-%Y"), show_date))
        return columns

    # ------------------------------------------------------------------
    # Per-show aggregation (mirrors _sum_by_account_and_branch, keyed on the
    # Show Date slotted into the ``analytic_id`` position).
    # ------------------------------------------------------------------
    def _sum_by_account_and_branch(self, filters, show_dates):
        """``{(account_id, show_date): balance}`` for show-tagged lines."""
        if not show_dates:
            return {}
        self.env.flush_all()
        params = [
            filters["date_from"],
            filters["date_to"],
            tuple(filters["company_ids"]) or (0,),
        ]
        sql = """
            SELECT aml.account_id,
                   am.x_custom_show_date AS show_date,
                   COALESCE(SUM(aml.balance), 0.0) AS balance
              FROM account_move_line aml
              JOIN account_move am ON am.id = aml.move_id
             WHERE aml.date >= %s
               AND aml.date <= %s
               AND aml.company_id IN %s
               AND aml.account_id IS NOT NULL
               AND am.x_custom_show_date IS NOT NULL
        """
        if filters.get("posted_only", True):
            sql += " AND aml.parent_state = %s"
            params.append("posted")
        else:
            sql += " AND aml.parent_state IN %s"
            params.append(("draft", "posted"))
        if filters.get("journal_ids"):
            sql += " AND aml.journal_id IN %s"
            params.append(tuple(filters["journal_ids"]))
        sql += " AND am.x_custom_show_date IN %s GROUP BY 1, 2"
        params.append(tuple(show_dates))

        self.env.cr.execute(sql, tuple(params))
        return {(row["account_id"], row["show_date"]): row["balance"] or 0.0 for row in self.env.cr.dictfetchall()}
