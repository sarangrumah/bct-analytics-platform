# -*- coding: utf-8 -*-
"""Shared abstract engine for every Custom Platform financial report.

A concrete report subclasses :class:`CustomReportEngine` and overrides
``_report_code`` / ``_report_title`` / ``_build_lines(filters)``.

The engine provides three responsibilities:

1.  **Filter normalisation** — coerce wizard input into a uniform
    ``filters`` dict every sub-report can rely on.
2.  **Raw-SQL aggregation** — high-throughput helpers
    (``_get_account_balances``, ``_get_move_lines_query``,
    ``_sum_by_account``) that read ``account_move_line`` once per
    report, parameterised, never string-concatenated.
3.  **Render context** — common header metadata + audit logging
    via :py:meth:`_log_report_run` (writes to ``pdp.audit_log``).
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from odoo import _, models
from odoo.tools.misc import format_date

_logger = logging.getLogger(__name__)


class CustomReportEngine(models.AbstractModel):
    """Abstract base for every ``custom.report.*`` model."""

    _name = "custom.report.engine"
    _description = "Custom Report Engine (Abstract Base)"

    # Subclasses MUST override these two.
    _report_code = None
    _report_title = None

    # ------------------------------------------------------------------
    # Filter defaults & normalisation
    # ------------------------------------------------------------------
    def _default_filters(self):
        """Return a fully-populated default filter dict.

        Keys:
            * ``date_from`` (``date``) — fiscal-year start by default.
            * ``date_to`` (``date``) — today by default.
            * ``company_ids`` (``list[int]``).
            * ``journal_ids`` (``list[int]``).
            * ``account_ids`` (``list[int]``).
            * ``partner_ids`` (``list[int]``).
            * ``posted_only`` (``bool``).
            * ``comparison`` (``bool``) plus matching comparison dates.
        """
        today = date.today()
        return {
            "date_from": today.replace(month=1, day=1),
            "date_to": today,
            "company_ids": list(self.env.companies.ids),
            "journal_ids": [],
            "account_ids": [],
            "partner_ids": [],
            "posted_only": True,
            "comparison": False,
            "comparison_date_from": None,
            "comparison_date_to": None,
        }

    def _get_query_filters(self, wizard):
        """Build a normalised filter dict from a wizard record.

        ``wizard`` is any record exposing the standard wizard fields
        (date_from, date_to, company_ids, journal_ids, partner_ids,
        posted_only). Missing fields fall back to engine defaults.
        """
        defaults = self._default_filters()
        if not wizard:
            return defaults
        getf = lambda name, default: (  # noqa: E731
            getattr(wizard, name, default) if hasattr(wizard, name) else default
        )
        filters = dict(defaults)
        filters["date_from"] = getf("date_from", defaults["date_from"])
        filters["date_to"] = getf("date_to", defaults["date_to"])
        company_ids = getf("company_ids", False)
        if company_ids:
            filters["company_ids"] = list(company_ids.ids)
        journal_ids = getf("journal_ids", False)
        if journal_ids:
            filters["journal_ids"] = list(journal_ids.ids)
        partner_ids = getf("partner_ids", False)
        if partner_ids:
            filters["partner_ids"] = list(partner_ids.ids)
        account_ids = getf("account_ids", False)
        if account_ids:
            filters["account_ids"] = list(account_ids.ids)
        posted_only = getf("posted_only", None)
        if posted_only is not None:
            filters["posted_only"] = bool(posted_only)
        return self._get_context_filters(filters)

    def _get_context_filters(self, filters=None):
        """Merge caller-supplied ``filters`` over defaults.

        Coerces string dates → ``date``; ensures ``date_from <= date_to``;
        materialises a comparison-period when ``comparison`` is set.
        """
        merged = self._default_filters()
        for key, value in (filters or {}).items():
            if value in (None, "", False) and key not in (
                "posted_only",
                "comparison",
            ):
                continue
            merged[key] = value

        for key in (
            "date_from",
            "date_to",
            "comparison_date_from",
            "comparison_date_to",
        ):
            value = merged.get(key)
            if isinstance(value, str) and value:
                merged[key] = date.fromisoformat(value)

        if merged["date_from"] and merged["date_to"] and (merged["date_from"] > merged["date_to"]):
            merged["date_from"], merged["date_to"] = (
                merged["date_to"],
                merged["date_from"],
            )

        if merged.get("comparison") and not merged.get("comparison_date_from"):
            span = (merged["date_to"] - merged["date_from"]).days + 1
            merged["comparison_date_to"] = merged["date_from"] - timedelta(days=1)
            merged["comparison_date_from"] = merged["comparison_date_to"] - timedelta(days=span - 1)

        # company_ids must always be a non-empty list — fall back to
        # current company so SQL ``IN ()`` cannot crash.
        if not merged.get("company_ids"):
            merged["company_ids"] = [self.env.company.id]

        return merged

    # ------------------------------------------------------------------
    # ORM domain helper (kept for sub-reports that need ORM iteration)
    # ------------------------------------------------------------------
    def _base_move_line_domain(self, filters):
        """ORM domain for ``account.move.line`` honouring ``filters``."""
        domain = [
            ("date", ">=", filters["date_from"]),
            ("date", "<=", filters["date_to"]),
            ("company_id", "in", list(filters["company_ids"])),
        ]
        if filters.get("posted_only", True):
            domain.append(("parent_state", "=", "posted"))
        else:
            domain.append(("parent_state", "in", ("draft", "posted")))
        # Omit document-only / memo journals (e.g. PPOB summary faktur) whose
        # GL effect must not reach the financial statements.
        domain.append(("journal_id.x_custom_report_excluded", "=", False))
        if filters.get("journal_ids"):
            domain.append(("journal_id", "in", list(filters["journal_ids"])))
        if filters.get("account_ids"):
            domain.append(("account_id", "in", list(filters["account_ids"])))
        if filters.get("partner_ids"):
            domain.append(("partner_id", "in", list(filters["partner_ids"])))
        return domain

    # ------------------------------------------------------------------
    # Raw SQL helpers — parameterised, never string-concatenated
    # ------------------------------------------------------------------
    def _get_move_lines_query(self, filters):
        """Return ``(query, params)`` selecting move-line rows.

        Subclasses may wrap this in their own aggregation. We rely on
        ``parent_state`` so the filter applies without an explicit JOIN.
        """
        params = [
            filters["date_from"],
            filters["date_to"],
            tuple(filters["company_ids"]) or (0,),
        ]
        query = """
            SELECT aml.id,
                   aml.account_id,
                   aml.partner_id,
                   aml.journal_id,
                   aml.move_id,
                   aml.date,
                   aml.name,
                   aml.ref,
                   aml.debit,
                   aml.credit,
                   aml.balance,
                   aml.amount_residual,
                   aml.date_maturity,
                   aml.company_id,
                   aml.parent_state,
                   aml.analytic_distribution
            FROM account_move_line aml
            WHERE aml.date >= %s
              AND aml.date <= %s
              AND aml.company_id IN %s
              AND aml.account_id IS NOT NULL
              AND aml.journal_id NOT IN (SELECT id FROM account_journal WHERE x_custom_report_excluded)
        """
        if filters.get("posted_only", True):
            query += " AND aml.parent_state = %s"
            params.append("posted")
        else:
            query += " AND aml.parent_state IN %s"
            params.append(("draft", "posted"))

        if filters.get("journal_ids"):
            query += " AND aml.journal_id IN %s"
            params.append(tuple(filters["journal_ids"]))
        if filters.get("account_ids"):
            query += " AND aml.account_id IN %s"
            params.append(tuple(filters["account_ids"]))
        if filters.get("partner_ids"):
            query += " AND aml.partner_id IN %s"
            params.append(tuple(filters["partner_ids"]))

        query += " ORDER BY aml.account_id, aml.date, aml.id"
        return query, tuple(params)

    def _get_account_balances(
        self,
        date_from=None,
        date_to=None,
        company_ids=None,
        journal_ids=None,
        *,
        filters=None,
    ):
        """Aggregate debit/credit/balance per account for the period.

        Three call shapes are accepted:

        * ``_get_account_balances(filters_dict)`` — pass the whole
          filter envelope as the single positional argument. Used by
          every concrete sub-report in this module.
        * ``_get_account_balances(date_from, date_to, company_ids,
          journal_ids)`` — positional, matches the spec.
        * ``_get_account_balances(filters=filters_dict)`` — keyword.

        Returns ``{account_id: {'account_id', 'account_code',
        'account_name', 'account_type', 'debit', 'credit', 'balance'}}``.
        """
        # Auto-detect the "single dict" shape so sub-reports can stay
        # terse: ``self._get_account_balances(filters)``.
        if filters is None and isinstance(date_from, dict):
            filters = date_from
        if filters is None:
            filters = self._get_context_filters(
                {
                    "date_from": date_from,
                    "date_to": date_to,
                    "company_ids": company_ids,
                    "journal_ids": journal_ids or [],
                }
            )
        return self._sum_by_account(filters)

    def _sum_by_account(self, filters, account_domain=None):
        """Raw-SQL per-account aggregation. Hot path for TB / GL / BS.

        ``account_domain`` is an optional extra ORM domain restricting
        the account set (resolved via a fast ``account.account`` search).
        """
        # Flush pending ORM writes so this raw SQL sees moves posted earlier in
        # the same transaction (post-then-report in one server action, or a
        # test). Without this, the last unflushed post is silently missed.
        self.env.flush_all()
        params = [
            filters["date_from"],
            filters["date_to"],
            tuple(filters["company_ids"]) or (0,),
        ]
        sql = """
            SELECT aml.account_id,
                   COALESCE(SUM(aml.debit), 0.0) AS debit,
                   COALESCE(SUM(aml.credit), 0.0) AS credit,
                   COALESCE(SUM(aml.balance), 0.0) AS balance
              FROM account_move_line aml
             WHERE aml.date >= %s
               AND aml.date <= %s
               AND aml.company_id IN %s
               AND aml.account_id IS NOT NULL
               AND aml.journal_id NOT IN (SELECT id FROM account_journal WHERE x_custom_report_excluded)
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
        if filters.get("account_ids"):
            sql += " AND aml.account_id IN %s"
            params.append(tuple(filters["account_ids"]))
        if filters.get("partner_ids"):
            sql += " AND aml.partner_id IN %s"
            params.append(tuple(filters["partner_ids"]))

        if account_domain:
            account_ids = self.env["account.account"].search(account_domain).ids
            if not account_ids:
                return {}
            sql += " AND aml.account_id IN %s"
            params.append(tuple(account_ids))

        sql += """
            GROUP BY aml.account_id
        """
        self.env.cr.execute(sql, tuple(params))
        rows = self.env.cr.dictfetchall()
        # ``code``/``name`` are no longer physical columns on
        # ``account_account`` (Odoo 19: ``code`` is a per-company computed
        # field backed by ``account.code.mapping``; ``name`` is translatable
        # JSONB). Resolve them through the ORM so per-company codes and the
        # active language are honoured -- mirroring how the rest of this
        # module reads ``account_id.code``.
        accounts = {acc.id: acc for acc in self.env["account.account"].browse([row["account_id"] for row in rows])}
        result = {}
        for row in rows:
            acc = accounts.get(row["account_id"])
            result[row["account_id"]] = {
                "account_id": row["account_id"],
                "account_code": self._account_code(acc),
                "account_name": acc.name if acc else "",
                "account_type": acc.account_type if acc else "",
                "debit": row["debit"] or 0.0,
                "credit": row["credit"] or 0.0,
                "balance": row["balance"] or 0.0,
            }
        return result

    # ------------------------------------------------------------------
    # Account-group hierarchy (GROUP 1 / GROUP 2)
    # ------------------------------------------------------------------
    def _account_groups(self, account_ids):
        """Map each account onto its two-level ``account.group`` ancestry.

        Returns ``{account_id: {g1_code, g1_name, g2_code, g2_name}}``, or an
        empty dict when the database holds no ``account.group`` at all — that
        is the signal for callers to fall back to ``account_type`` sections.

        ``account.account.group_id`` is a non-stored computed field in Odoo 19
        (resolved from the code prefix), so it must be read through the ORM on
        a whole recordset rather than joined in SQL. ``g1`` is the top-most
        ancestor; ``g2`` is the account's own group when it is not already the
        top-most one.
        """
        if not account_ids or not self.env["account.group"].search_count([]):
            return {}
        result = {}
        for account in self.env["account.account"].browse(account_ids):
            group = account.group_id
            if not group:
                continue
            root = group
            while root.parent_id:
                root = root.parent_id
            leaf = group if group != root else self.env["account.group"]
            result[account.id] = {
                "g1_code": root.code_prefix_start or "",
                "g1_name": root.name or "",
                "g2_code": leaf.code_prefix_start or "",
                "g2_name": leaf.name or "",
            }
        return result

    def _group_display_code(self, prefix, width=10):
        """Render a group prefix the way Finance writes it: ``51`` → ``5100000000``."""
        prefix = prefix or ""
        return prefix.ljust(width, "0") if prefix else ""

    def _grouped_section(self, label, rows):
        """Nest signed ``rows`` under their GROUP 2 headers.

        ``rows`` are balance dicts already carrying ``signed_balance``. When
        the database has no ``account.group`` the section degrades to the flat
        ``accounts`` list every renderer already understands.
        """
        rows = sorted(rows, key=lambda r: r["account_code"] or "")
        subtotal = sum(r["signed_balance"] for r in rows)
        groups = self._account_groups([r["account_id"] for r in rows])
        if not groups:
            return {"type": "section", "label": label, "accounts": rows, "subtotal": subtotal}

        buckets = {}
        ungrouped = []
        for row in rows:
            info = groups.get(row["account_id"])
            if not info or not info["g2_code"]:
                ungrouped.append(row)
                continue
            bucket = buckets.setdefault(
                info["g2_code"],
                {
                    "code": self._group_display_code(info["g2_code"]),
                    "label": info["g2_name"],
                    "accounts": [],
                    "subtotal": 0.0,
                },
            )
            bucket["accounts"].append(row)
            bucket["subtotal"] += row["signed_balance"]
        return {
            "type": "section",
            "label": label,
            "subgroups": [buckets[key] for key in sorted(buckets)],
            "accounts": ungrouped,
            "subtotal": subtotal,
        }

    # ------------------------------------------------------------------
    # Branch (Operating Unit) analytic plan
    # ------------------------------------------------------------------
    def _branch_plan(self):
        """The ``account.analytic.plan`` that carries the branch dimension.

        Tenant-configurable so this module stays generic: the apparel brand calls it
        "Operating Unit", another tenant may not have one at all (empty
        recordset — callers must cope).
        """
        name = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("custom_accounting_reports.branch_plan_name", "Operating Unit")
        )
        return self.env["account.analytic.plan"].search([("name", "=", name)], limit=1)

    def _analytic_ids_from_distribution(self, distribution):
        """Every analytic id referenced by an ``analytic_distribution`` JSON.

        Odoo joins ids from several plans into one comma-separated key.
        """
        ids = set()
        for key in (distribution or {}).keys():
            for part in str(key).split(","):
                part = part.strip()
                if part.isdigit():
                    ids.add(int(part))
        return ids

    def _account_code(self, account):
        """Return an account's code resolved in its OWN company.

        ``account.account.code`` is company-dependent in Odoo 19 (stored per
        company in ``code_store``). Reading it in the ambient ``self.env.company``
        yields a blank for accounts that belong to a different company -- which is
        why a report scoped to company B, but viewed from a session whose active
        company is A, showed an empty Code column. Resolve the code in the
        account's own company so it is correct regardless of the active company.
        """
        if not account:
            return ""
        company = account.company_ids[:1] or self.env.company
        return account.with_company(company).code or ""

    def _opt(self, record, field_name, default=""):
        """Read ``field_name`` off ``record`` only when the field exists.

        Lets tax reports enrich rows with optional fields from modules that may
        not be installed (``custom_coretax`` / ``custom_tax_id``) without a hard
        dependency — the value is ``default`` when the field is absent.
        """
        if record and field_name in record._fields:
            return record[field_name] or default
        return default

    # ------------------------------------------------------------------
    # Render-context helpers
    # ------------------------------------------------------------------
    def _get_company_currency(self, filters=None):
        filters = filters or {}
        if filters.get("company_ids"):
            company = self.env["res.company"].browse(filters["company_ids"][0])
            if company.exists():
                return company.currency_id
        return self.env.company.currency_id

    def _format_amount(self, value, currency=None):
        """Locale-aware monetary formatting.

        Falls back to a plain two-decimal string if no currency.
        """
        if currency is None:
            currency = self._get_company_currency()
        value = value or 0.0
        if currency:
            rounded = currency.round(value)
            symbol = currency.symbol or ""
            position = currency.position or "before"
            formatted = f"{rounded:,.{currency.decimal_places}f}"
            if position == "before":
                return f"{symbol} {formatted}".strip()
            return f"{formatted} {symbol}".strip()
        return f"{value:,.2f}"

    def _format_date_id(self, value):
        """Format ``value`` as Indonesian ``dd/mm/yyyy``."""
        if not value:
            return ""
        if isinstance(value, str):
            try:
                value = date.fromisoformat(value)
            except ValueError:
                return value
        return value.strftime("%d/%m/%Y")

    def _coverage_banner(self, filters):
        """Header row consumed by QWeb templates."""
        return [
            {
                "type": "coverage",
                "date_from": filters["date_from"],
                "date_to": filters["date_to"],
                "date_from_str": self._format_date_id(filters["date_from"]),
                "date_to_str": self._format_date_id(filters["date_to"]),
                "posted_only": filters.get("posted_only", True),
            }
        ]

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------
    def _build_lines(self, filters):
        raise NotImplementedError(
            _(
                "Report %(code)s must override _build_lines().",
                code=self._report_code or self._name,
            )
        )

    def _compute(self, filters=None):
        """Public entry point used by wizards.

        Returns the render context dict (header metadata + lines).
        Also writes a PDP audit row so we know who ran which report.
        """
        filters = self._get_context_filters(filters)
        companies = self.env["res.company"].browse(filters["company_ids"])
        currency = self._get_company_currency(filters)
        lines = self._build_lines(filters)
        self._log_report_run(filters)
        return {
            "report_code": self._report_code,
            "report_title": self._report_title or self._name,
            "filters": filters,
            "options": filters,
            "lines": lines,
            "company": companies[:1],
            "company_names": ", ".join(companies.mapped("name")),
            "currency": currency,
            "date_from": filters["date_from"],
            "date_to": filters["date_to"],
            "date_from_str": format_date(self.env, filters["date_from"]),
            "date_to_str": format_date(self.env, filters["date_to"]),
            "date_from_id": self._format_date_id(filters["date_from"]),
            "date_to_id": self._format_date_id(filters["date_to"]),
        }

    # ------------------------------------------------------------------
    # On-screen interactive table
    # ------------------------------------------------------------------
    def _drilldown_enabled(self):
        """Whether report rows/cells link into the General Ledger.

        Per-database opt-in (``custom_accounting_reports.drilldown_enabled``,
        default off): this addon is shared by every tenant DB on the same
        container, so a code deploy alone would switch the feature on
        everywhere. Rolled out to prd_retail_begbal only for now.
        """
        return self.env["ir.config_parameter"].sudo().get_param("custom_accounting_reports.drilldown_enabled") in (
            "1",
            "true",
            "True",
        )

    def get_report_table(self, options=None, context_extra=None):
        """Compute the report and return a JSON-serialisable payload the
        OWL client action renders as an interactive on-screen table.

        Reuses the exact same contract as the Excel exporter:
        :py:meth:`_xlsx_columns` (column spec) + :py:meth:`_build_lines`
        (row data, via ``_compute``). ``context_extra`` carries per-report
        context knobs (e.g. GL's ``gl_layout``/``gl_columns``).
        """
        report = self.with_context(**(context_extra or {}))
        ctx = report._compute(options)
        columns = report._xlsx_columns()
        currency = ctx.get("currency")
        drilldown = report._drilldown_enabled()
        return {
            "title": ctx.get("report_title"),
            "company_names": ctx.get("company_names"),
            "period": {
                "date_from": ctx.get("date_from_id"),
                "date_to": ctx.get("date_to_id"),
            },
            "currency": {
                "symbol": currency.symbol if currency else "",
                "position": currency.position if currency else "before",
                "decimals": currency.decimal_places if currency else 2,
            },
            "columns": [
                {
                    "header": col.get("header", ""),
                    "field": col["field"],
                    "kind": col.get("kind", "text"),
                    "width": col.get("width", 16),
                    # Screen-only: non-empty = this cell is its own GL link
                    # (see ``get_drilldown_action``'s ``scope``).
                    "drilldown": (col.get("drilldown") or "") if drilldown else "",
                }
                for col in columns
            ],
            "lines": report._flatten_for_screen(ctx.get("lines", []), columns),
        }

    def _first_text_field(self, columns):
        """The first non-numeric/non-date column field — where a total
        row's ``label`` is rendered when it has no account field."""
        return next(
            (col["field"] for col in columns if col.get("kind") not in ("number", "date")),
            None,
        )

    def _screen_row(self, line, columns, first_text=None):
        """Coerce one ``_build_lines`` dict into a display row
        ``{type, level, values}`` keyed by the column fields."""
        values = {}
        for col in columns:
            field = col["field"]
            kind = col.get("kind")
            value = line.get(field)
            if kind == "date":
                value = self._format_date_id(value)
            elif kind == "number":
                # ``None`` = this row has no amount for this column (heading
                # rows); the client renders it blank instead of ``0.00``.
                missing = value is None or value is False or value == ""
                value = None if missing else float(value)
            else:
                # Some reports declare their Date column as ``text`` (their
                # XLSX body formats it by hand) — do the same here rather
                # than shipping a raw date object to the client.
                if isinstance(value, date):
                    value = self._format_date_id(value)
                value = value if value not in (None, False) else ""
            values[field] = value
        # Total-type rows often carry only a ``label`` + numbers.
        if first_text and not values.get(first_text) and line.get("label"):
            values[first_text] = line["label"]
        row = {
            "type": line.get("type") or "data",
            "level": line.get("level", 0),
            "values": values,
        }
        # Carried outside ``values`` (it is not a column): the client turns an
        # account row into a General Ledger drill-down link. ``get_param`` is
        # ormcached, so testing the gate per row is cheap.
        if line.get("account_id") and self._drilldown_enabled():
            row["account_id"] = line["account_id"]
        return row

    def _flatten_for_screen(self, lines, columns):
        """Default flattener: pass rows through, drop the coverage banner,
        tag each with ``type``/``level``. Reports with nested ``section``
        rows (Balance Sheet / P&L / Cash Flow) override this to call
        :py:meth:`_flatten_sectioned`."""
        first_text = self._first_text_field(columns)
        return [self._screen_row(line, columns, first_text) for line in lines if line.get("type") != "coverage"]

    def _flatten_grouped(
        self,
        lines,
        columns,
        group_type,
        heading,
        totals,
        total_label=None,
        opening_field=None,
    ):
        """Screen flattener for the reports whose ``_build_lines`` nests
        every movement under a group row — General Ledger (accounts),
        Partner Ledger (partners), Advance (accounts).

        The default flattener passes rows straight through, which for
        these reports means one *blank* row per group (the group dict
        carries none of the column fields) and no movements at all: the
        Excel export looks right while the screen looks empty. This
        mirrors the matching :py:meth:`_xlsx_body` instead — group
        heading, its lines, group total, then the grand total.

        ``heading(line)`` labels the group row, ``totals`` maps a column
        field onto the group key holding that total, ``total_label(line)``
        labels the total row, and ``opening_field`` (when set) is the
        column that shows the group's opening figure on the heading row.
        """
        first_text = self._first_text_field(columns)
        # ``self.env._`` rather than the module-level ``_``: the latter sniffs
        # the caller frame for a language and logs a warning per row when
        # called from a lambda.
        total_label = total_label or (lambda line: self.env._("Total"))

        def totals_row(row_type, label, source):
            values = {first_text: label}
            values.update({field: float(source.get(key) or 0.0) for field, key in totals.items()})
            return {"type": row_type, "level": 0, "values": values}

        out = []
        for line in lines:
            ltype = line.get("type")
            if ltype == "coverage":
                continue
            if ltype == group_type:
                values = {first_text: heading(line)}
                if opening_field:
                    values[opening_field] = float(line.get("opening") or 0.0)
                out.append({"type": "group", "level": 0, "values": values})
                for nested in line.get("lines", []):
                    row = self._screen_row(nested, columns, first_text)
                    row["level"] = 1
                    out.append(row)
                out.append(totals_row("total", total_label(line), line))
            elif ltype == "grand_total":
                out.append(totals_row("grand_total", line.get("label") or self.env._("Grand Total"), line))
            else:
                out.append(self._screen_row(line, columns, first_text))
        return out

    def _text_fields(self, columns):
        """The first two non-numeric column fields — where a group header
        writes its code and its name."""
        fields = [col["field"] for col in columns if col.get("kind") not in ("number", "date")]
        return (fields + [None, None])[:2]

    def _flatten_sectioned(self, lines, columns, section_heading=False):
        """Flatten ``header``/``section``/``total`` reports (Balance Sheet,
        P&L, Cash Flow) into display rows — the on-screen analogue of
        :py:meth:`_xlsx_sectioned_body`.

        ``section_heading`` mirrors the same flag on the XLSX body: when
        ``True`` (P&L / Cash Flow) each section emits its own ``label`` as a
        heading row; when ``False`` (Balance Sheet) sections rely on the
        explicit ``header`` lines that precede them.

        A section carrying ``subgroups`` renders one extra heading level
        (GROUP 2), pushing its accounts to ``level`` 2.
        """
        first_text, second_text = self._text_fields(columns)
        out = []

        def emit_accounts(accounts, level):
            for acc in accounts:
                row = self._screen_row(acc, columns, first_text)
                row["level"] = level
                out.append(row)

        for line in lines:
            ltype = line.get("type")
            if ltype == "coverage":
                continue
            if ltype == "header":
                out.append({"type": "header", "level": 0, "values": {first_text: line.get("label") or ""}})
            elif ltype == "section":
                if section_heading and line.get("label"):
                    out.append({"type": "section", "level": 0, "values": {first_text: line["label"]}})
                subgroups = line.get("subgroups") or []
                for sub in subgroups:
                    values = {first_text: sub.get("code") or ""}
                    if second_text:
                        values[second_text] = sub.get("label") or ""
                    out.append({"type": "group", "level": 1, "values": values})
                    emit_accounts(sub.get("accounts", []), 2)
                emit_accounts(line.get("accounts", []), 2 if subgroups else 1)
            else:
                out.append(self._screen_row(line, columns, first_text))
        return out

    # ------------------------------------------------------------------
    # XLSX export
    # ------------------------------------------------------------------
    def _xlsx_columns(self):
        """Column spec driving the generic XLSX exporter.

        Return a list of dicts, one per column::

            {"header": "Code", "field": "account_code",
             "kind": "text"|"number", "width": 14}

        ``field`` is read from each line dict produced by
        :py:meth:`_build_lines`. Subclasses that support Excel export
        must override this.
        """
        raise NotImplementedError(
            _(
                "Report %(code)s does not support XLSX export (override _xlsx_columns()).",
                code=self._report_code or self._name,
            )
        )

    def _xlsx_formats(self, workbook):
        """Reusable cell formats shared by every report's XLSX body."""
        return {
            "title": workbook.add_format({"bold": True, "font_size": 14, "align": "center"}),
            "meta": workbook.add_format({"align": "center", "font_size": 10}),
            "header": workbook.add_format(
                {
                    "bold": True,
                    "border": 1,
                    "bg_color": "#D9E1F2",
                    "align": "center",
                    "valign": "vcenter",
                    "text_wrap": True,
                }
            ),
            "text": workbook.add_format({"border": 1}),
            "num": workbook.add_format({"border": 1, "num_format": "#,##0.00"}),
            "total_text": workbook.add_format({"border": 1, "bold": True, "bg_color": "#F2F2F2"}),
            "total_num": workbook.add_format(
                {
                    "border": 1,
                    "bold": True,
                    "bg_color": "#F2F2F2",
                    "num_format": "#,##0.00",
                }
            ),
            # Hierarchical reports (GL group headers, BS section headers).
            "group_text": workbook.add_format({"border": 1, "bold": True, "bg_color": "#EDEDED"}),
            "group_num": workbook.add_format(
                {
                    "border": 1,
                    "bold": True,
                    "bg_color": "#EDEDED",
                    "num_format": "#,##0.00",
                }
            ),
            "section": workbook.add_format({"bold": True, "font_size": 11, "bg_color": "#FCE4D6"}),
        }

    def _xlsx_export(self, filters=None):
        """Compute the report and render it to XLSX. Returns raw bytes.

        Writes the common title/company/period banner, then delegates the
        data rows to :py:meth:`_xlsx_body` (default = flat table driven by
        :py:meth:`_xlsx_columns`). Reports with hierarchical lines override
        ``_xlsx_body``.
        """
        import io
        import xlsxwriter

        ctx = self._compute(filters)
        columns = self._xlsx_columns()
        last_col = max(len(columns) - 1, 0)

        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {"in_memory": True})
        sheet = workbook.add_worksheet((self._report_title or "Report")[:31])
        fmts = self._xlsx_formats(workbook)

        for col_idx, col in enumerate(columns):
            sheet.set_column(col_idx, col_idx, col.get("width", 16))

        row = 0
        sheet.merge_range(row, 0, row, last_col, ctx.get("report_title", ""), fmts["title"])
        row += 1
        if ctx.get("company_names"):
            sheet.merge_range(row, 0, row, last_col, ctx["company_names"], fmts["meta"])
            row += 1
        period = "%s s/d %s" % (
            ctx.get("date_from_id") or "",
            ctx.get("date_to_id") or "",
        )
        sheet.merge_range(row, 0, row, last_col, period, fmts["meta"])
        row += 2

        self._xlsx_body(sheet, ctx, columns, fmts, row)

        workbook.close()
        return output.getvalue()

    def _xlsx_body(self, sheet, ctx, columns, fmts, start_row):
        """Render the data rows. Returns the next free row.

        Default implementation = flat table driven by ``columns``. Lines
        whose ``type`` is ``coverage`` are skipped; ``grand_total`` /
        ``total`` / ``subtotal`` rows are bold, with any ``label`` falling
        into the first text column.
        """
        first_text_col = next(
            (i for i, c in enumerate(columns) if c.get("kind") not in ("number", "date")),
            0,
        )
        row = start_row
        for col_idx, col in enumerate(columns):
            sheet.write(row, col_idx, col.get("header", ""), fmts["header"])
        sheet.freeze_panes(row + 1, 0)
        row += 1

        for line in ctx.get("lines", []):
            if line.get("type") == "coverage":
                continue
            is_total = line.get("type") in ("grand_total", "total", "subtotal")
            for col_idx, col in enumerate(columns):
                kind = col.get("kind")
                value = line.get(col["field"])
                if kind == "number":
                    fmt = fmts["total_num"] if is_total else fmts["num"]
                    if value is None or value is False or value == "":
                        sheet.write(row, col_idx, "", fmt)
                    else:
                        sheet.write_number(row, col_idx, float(value), fmt)
                    continue
                fmt = fmts["total_text"] if is_total else fmts["text"]
                if kind == "date":
                    value = self._format_date_id(value)
                elif not value and col_idx == first_text_col:
                    value = line.get("label") or ""
                sheet.write(row, col_idx, value or "", fmt)
            row += 1
        return row

    def _xlsx_sectioned_body(
        self,
        sheet,
        ctx,
        fmts,
        start_row,
        amount_header="Amount",
        secondary=None,
        section_heading=False,
    ):
        """Render reports whose lines are header/section/total rows
        (Balance Sheet, P&L, Cash Flow).

        Columns: ``Code | Account | <amount_header> [| secondary[0]]``.
        ``secondary`` = ``(header_text, line_key)`` adds a 4th column read
        from total-type lines; ``None`` = three columns.
        ``section_heading`` = emit a heading row for each section's own
        ``label`` (P&L/CF) instead of relying on explicit ``header`` lines
        (Balance Sheet).
        """
        lines = ctx.get("lines", [])
        has_secondary = secondary is not None
        last_col = 3 if has_secondary else 2
        row = start_row

        sheet.write(row, 0, "Code", fmts["header"])
        sheet.write(row, 1, "Account", fmts["header"])
        sheet.write(row, 2, amount_header, fmts["header"])
        if has_secondary:
            sheet.set_column(3, 3, 20)
            sheet.write(row, 3, secondary[0], fmts["header"])
        sheet.freeze_panes(row + 1, 0)
        row += 1

        def write_accounts(accounts, row):
            for acc in accounts:
                sheet.write(row, 0, acc.get("account_code") or "", fmts["text"])
                sheet.write(row, 1, acc.get("account_name") or "", fmts["text"])
                sheet.write_number(row, 2, float(acc.get("signed_balance") or 0.0), fmts["num"])
                if has_secondary:
                    sheet.write(row, 3, "", fmts["text"])
                row += 1
            return row

        for line in lines:
            ltype = line.get("type")
            if ltype == "header":
                sheet.merge_range(row, 0, row, last_col, line.get("label") or "", fmts["section"])
                row += 1
            elif ltype == "section":
                if section_heading and line.get("label"):
                    sheet.merge_range(row, 0, row, last_col, line["label"], fmts["group_text"])
                    row += 1
                for sub in line.get("subgroups") or []:
                    sheet.write(row, 0, sub.get("code") or "", fmts["group_text"])
                    sheet.write(row, 1, sub.get("label") or "", fmts["group_text"])
                    sheet.write(row, 2, "", fmts["group_text"])
                    if has_secondary:
                        sheet.write(row, 3, "", fmts["group_text"])
                    row += 1
                    row = write_accounts(sub.get("accounts", []), row)
                row = write_accounts(line.get("accounts", []), row)
            elif ltype in ("total", "subtotal", "grand_total", "check"):
                sheet.merge_range(row, 0, row, 1, line.get("label") or "", fmts["total_text"])
                sheet.write_number(row, 2, float(line.get("signed_balance") or 0.0), fmts["total_num"])
                if has_secondary:
                    val = line.get(secondary[1])
                    if val is None:
                        sheet.write(row, 3, "", fmts["total_text"])
                    else:
                        sheet.write_number(row, 3, float(val), fmts["total_num"])
                row += 1
        return row

    def _xlsx_action(self, filters, filename):
        """Build the XLSX, stash it as an ``ir.attachment`` and return an
        ``act_url`` download action. Convenience wrapper for wizards."""
        import base64

        content = self._xlsx_export(filters)
        attachment = self.env["ir.attachment"].create(
            {
                "name": filename,
                "type": "binary",
                "datas": base64.b64encode(content),
                "mimetype": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            }
        )
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attachment.id,
            "target": "self",
        }

    # ------------------------------------------------------------------
    # PDP audit hook — record every report run
    # ------------------------------------------------------------------
    def _log_report_run(self, filters):
        """Insert a row into ``pdp.audit_log`` (best-effort).

        Mirrors the raw-SQL pattern used by ``pdp.audited.mixin``. Never
        raises — a logging failure must not abort the report.
        """
        try:
            user = self.env.user
            payload = {
                "report_code": self._report_code,
                "report_title": self._report_title,
                "date_from": (filters["date_from"].isoformat() if filters.get("date_from") else None),
                "date_to": (filters["date_to"].isoformat() if filters.get("date_to") else None),
                "company_ids": list(filters.get("company_ids") or []),
                "journal_ids": list(filters.get("journal_ids") or []),
                "partner_ids": list(filters.get("partner_ids") or []),
                "posted_only": bool(filters.get("posted_only", True)),
            }
            self.env.cr.execute(
                """
                INSERT INTO pdp.audit_log (
                    actor_user_id, actor_login, tenant_db,
                    model_name, res_id, action,
                    field_changes, classification,
                    ip_address, user_agent, request_id, reason
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s::jsonb, %s,
                    NULL, NULL, NULL, %s
                )
                """,
                (
                    user.id if user else None,
                    user.login if user else None,
                    self.env.cr.dbname,
                    self._name,
                    None,
                    "export",
                    json.dumps(payload, default=str),
                    "financial",
                    "Report run: %s" % (self._report_title or self._name),
                ),
            )
        except Exception as exc:  # pragma: no cover - defensive
            _logger.warning(
                "custom_accounting_reports: PDP audit log skipped for %s: %s",
                self._name,
                exc,
            )
