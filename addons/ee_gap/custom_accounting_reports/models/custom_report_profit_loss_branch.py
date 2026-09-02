# -*- coding: utf-8 -*-
"""Profit & Loss split across branches (Operating Units).

Same rows as the ordinary Profit & Loss — the account-group hierarchy and
the running-profit milestones are inherited verbatim — but one amount column
per branch instead of Period/YTD.

A branch is an ``account.analytic.account`` on the plan named by
``custom_accounting_reports.branch_plan_name``. Journal items that carry no
branch at all (opening balances, head-office postings) land in the head-office
column, which is how Finance reads the statement.
"""

from odoo import models

from .custom_report_profit_loss import INCOME_TYPES


class CustomReportProfitLossBranch(models.AbstractModel):
    _name = "custom.report.profit.loss.branch"
    _inherit = "custom.report.profit.loss"
    _description = "Custom Profit & Loss by Branch"

    _report_code = "profit_loss_branch"
    _report_title = "Profit & Loss by Branch"

    HQ_KEY = "hq"

    # ------------------------------------------------------------------
    # Branch columns
    # ------------------------------------------------------------------
    def _head_office_analytic(self):
        """The analytic account standing for the head office, when the tenant
        localisation names one (``res.company.l10n_ho_analytic_id``)."""
        return self._opt(self.env.company, "l10n_ho_analytic_id", None) or None

    def _branch_columns(self):
        """``[(key, label, analytic_id)]`` — head office first, then stores.

        The head-office column has no ``analytic_id``: it absorbs both its own
        tagged lines and every untagged line, so it is computed as a residual.
        """
        plan = self._branch_plan()
        head_office = self._head_office_analytic()
        columns = [(self.HQ_KEY, head_office.name if head_office else "Head Quarter", None)]
        if not plan:
            return columns
        # An analytic plan is not company-scoped but its accounts are: only the
        # active companies' branches belong in this statement.
        stores = self.env["account.analytic.account"].search(
            [
                ("plan_id", "=", plan.id),
                ("company_id", "in", self.env.companies.ids + [False]),
            ],
            order="name",
        )
        if head_office:
            stores -= head_office
        columns.extend(("ou_%s" % store.id, store.name, store.id) for store in stores)
        return columns

    def _xlsx_columns(self):
        columns = [
            {"header": "Account No", "field": "account_code", "kind": "text", "width": 16},
            {"header": "Description", "field": "account_name", "kind": "text", "width": 44},
        ]
        columns.extend(
            {"header": label, "field": key, "kind": "number", "width": 20}
            for key, label, _analytic_id in self._branch_columns()
        )
        return columns

    # One column per branch: the parent's three-column sectioned renderers
    # cannot express that, so fall back to the engine's generic flat table
    # (which is driven entirely by ``_xlsx_columns``).
    def _xlsx_body(self, sheet, ctx, columns, fmts, start_row):
        engine = self.env["custom.report.engine"]
        return engine._xlsx_body(sheet, ctx, columns, fmts, start_row)

    def _flatten_for_screen(self, lines, columns):
        return self.env["custom.report.engine"]._flatten_for_screen(lines, columns)

    # ------------------------------------------------------------------
    # Per-branch aggregation
    # ------------------------------------------------------------------
    def _sum_by_account_and_branch(self, filters, analytic_ids):
        """``{(account_id, analytic_id): balance}`` for branch-tagged lines.

        ``analytic_distribution`` is a JSONB map of ``"<id>[,<id>...]" ->
        percentage``; ids from several plans share one key, so each key is
        exploded and only the branch plan's ids are kept. The percentage
        applies to the whole key.
        """
        if not analytic_ids:
            return {}
        self.env.flush_all()
        params = [
            filters["date_from"],
            filters["date_to"],
            tuple(filters["company_ids"]) or (0,),
        ]
        sql = """
            SELECT aml.account_id,
                   part.analytic_id::int AS analytic_id,
                   COALESCE(SUM(aml.balance * kv.value::numeric / 100.0), 0.0) AS balance
              FROM account_move_line aml
             CROSS JOIN LATERAL jsonb_each_text(aml.analytic_distribution) AS kv(key, value)
             CROSS JOIN LATERAL unnest(string_to_array(kv.key, ',')) AS part(analytic_id)
             WHERE aml.date >= %s
               AND aml.date <= %s
               AND aml.company_id IN %s
               AND aml.account_id IS NOT NULL
               AND part.analytic_id ~ '^[0-9]+$'
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
        sql += " AND part.analytic_id::int IN %s GROUP BY 1, 2"
        params.append(tuple(analytic_ids))

        self.env.cr.execute(sql, tuple(params))
        return {(row["account_id"], row["analytic_id"]): row["balance"] or 0.0 for row in self.env.cr.dictfetchall()}

    def _account_row(self, row, columns, per_branch):
        """One account line carrying an amount per branch column."""
        sign = -1.0 if row["account_type"] in INCOME_TYPES else 1.0
        values = {}
        stores_total = 0.0
        for key, _label, analytic_id in columns:
            if analytic_id is None:
                continue
            balance = per_branch.get((row["account_id"], analytic_id), 0.0)
            stores_total += balance
            values[key] = sign * balance
        values[self.HQ_KEY] = sign * (row["balance"] - stores_total)
        return dict(row, **values)

    # ------------------------------------------------------------------
    # Lines
    # ------------------------------------------------------------------
    def _build_lines(self, filters):
        columns = self._branch_columns()
        keys = [key for key, _label, _analytic_id in columns]
        balances = self._get_account_balances(filters)
        per_branch = self._sum_by_account_and_branch(filters, [aid for _k, _l, aid in columns if aid])
        groups = self._account_groups(list(balances))

        buckets = {key: [] for key, _label, _income in self._pl_buckets()}
        for row in balances.values():
            bucket = self._bucket_key(row, groups.get(row["account_id"]))
            if bucket:
                buckets[bucket].append(self._account_row(row, columns, per_branch))

        def totals(rows):
            return {key: sum(row.get(key, 0.0) for row in rows) for key in keys}

        sums = {bucket: totals(rows) for bucket, rows in buckets.items()}

        def combine(*terms):
            """``terms`` are ``(sign, bucket)`` pairs summed per branch column."""
            return {key: sum(sign * sums[bucket][key] for sign, bucket in terms) for key in keys}

        gross = combine((1, "revenue"), (-1, "cogs"))
        operating = {key: gross[key] - sums["opex"][key] for key in keys}
        before_tax = {key: operating[key] + sums["other_income"][key] - sums["other_expense"][key] for key in keys}
        after_tax = {key: before_tax[key] - sums["tax"][key] for key in keys}

        labels = {key: label for key, label, _income in self._pl_buckets()}
        lines = []

        def section(bucket):
            rows = buckets[bucket]
            if not rows:
                return
            lines.append({"type": "section", "label": labels[bucket]})
            for row in sorted(rows, key=lambda r: r["account_code"] or ""):
                lines.append(dict(row, type="data", level=1))

        def total(label, values, ltype="total"):
            lines.append(dict(values, type=ltype, label=label))

        section("revenue")
        total("Total Revenue", sums["revenue"])
        section("cogs")
        total("Total COGS", sums["cogs"])
        total("Gross Profit", gross)
        section("opex")
        total("Total Operating Expenses", sums["opex"])
        total("Operating Profit", operating)
        section("other_income")
        section("other_expense")
        total("Net Profit / (Loss) Before Tax", before_tax)
        section("tax")
        total("Net Profit / (Loss)", after_tax, ltype="grand_total")
        if buckets["oci"]:
            section("oci")
            total(
                "Total Comprehensive Income",
                {key: after_tax[key] + sums["oci"][key] for key in keys},
                ltype="grand_total",
            )
        return lines
