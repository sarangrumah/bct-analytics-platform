# -*- coding: utf-8 -*-
"""Declare a consolidation perimeter (parent + subsidiaries + eliminations)."""

from __future__ import annotations

from datetime import date
from typing import Any

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ConsolidationConfig(models.Model):
    _name = "account.consolidation.config"
    _description = "Consolidation Perimeter"
    _order = "fiscal_year desc, name"

    name = fields.Char(required=True, default=lambda self: f"Consolidation {date.today().year}")
    active = fields.Boolean(default=True)
    fiscal_year = fields.Integer(required=True, default=lambda self: date.today().year)

    parent_company_id = fields.Many2one("res.company", required=True, ondelete="restrict")
    subsidiary_ids = fields.Many2many(
        "res.company",
        "consol_config_subsidiary_rel",
        "config_id",
        "company_id",
        string="Subsidiaries",
    )

    # Accounts whose balances cancel out across companies (intercompany
    # receivables/payables, intercompany revenue/cost, dividend payables, etc.).
    elimination_account_ids = fields.Many2many(
        "account.account",
        "consol_config_elim_account_rel",
        "config_id",
        "account_id",
        string="Elimination Accounts",
        help="Balances on these accounts within the perimeter are netted to zero.",
    )

    # Optional FX policy when subsidiaries report in different currencies
    presentation_currency_id = fields.Many2one(
        "res.currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )

    notes = fields.Text()

    @api.constrains("parent_company_id", "subsidiary_ids")
    def _check_perimeter(self):
        for rec in self:
            if rec.parent_company_id in rec.subsidiary_ids:
                raise ValidationError(_("Parent cannot also appear in subsidiaries."))
            if not rec.subsidiary_ids and rec.parent_company_id:
                # Single-company "consolidation" still legal but warn
                pass

    # ------------------------------------------------------------------

    def perimeter_company_ids(self):
        self.ensure_one()
        return self.parent_company_id | self.subsidiary_ids

    # ------------------------------------------------------------------
    # Core arithmetic — returns rows ready for rendering
    # ------------------------------------------------------------------

    def _compute_balances(self, date_from, date_to, account_types=None):
        """Return per-company per-account balance rows for the perimeter.

        ``account_types`` filters by ``account.account.account_type`` (e.g.
        ``['asset_receivable', 'asset_current', ...]``). None = all.

        Returns: list of dicts
          {company_id, company_name, account_id, account_code, account_name,
           account_type, debit, credit, balance}
        """
        self.ensure_one()
        if not date_from or not date_to:
            raise ValidationError(_("Date range required."))

        perimeter = self.perimeter_company_ids()
        domain = [
            ("parent_state", "=", "posted"),
            ("company_id", "in", perimeter.ids),
            ("date", ">=", date_from),
            ("date", "<=", date_to),
        ]
        if account_types:
            domain.append(("account_id.account_type", "in", account_types))

        AML = self.env["account.move.line"].sudo()
        # Aggregate
        groups = AML.read_group(
            domain,
            ["debit:sum", "credit:sum"],
            ["account_id", "company_id"],
            lazy=False,
        )
        accounts = self.env["account.account"].browse({g["account_id"][0] for g in groups if g.get("account_id")})
        companies = self.env["res.company"].browse({g["company_id"][0] for g in groups if g.get("company_id")})
        accounts_map = {a.id: a for a in accounts}
        companies_map = {c.id: c for c in companies}

        rows: list[dict[str, Any]] = []
        for g in groups:
            if not g.get("account_id") or not g.get("company_id"):
                continue
            a = accounts_map[g["account_id"][0]]
            c = companies_map[g["company_id"][0]]
            debit = g.get("debit", 0.0) or 0.0
            credit = g.get("credit", 0.0) or 0.0
            # Odoo 19: account.code is a compute that reads code_store via
            # env.company.root_id; rebind the account to the row's company to
            # get the per-company code instead of False.
            account_code = a.with_company(c).code
            rows.append(
                {
                    "company_id": c.id,
                    "company_name": c.name,
                    "account_id": a.id,
                    "account_code": account_code,
                    "account_name": a.name,
                    "account_type": a.account_type,
                    "debit": debit,
                    "credit": credit,
                    "balance": debit - credit,
                }
            )
        return rows

    def _compute_eliminations(self, balance_rows):
        """Return per-account elimination row totals.

        For each ``elimination_account_id``, we sum the balance across the
        perimeter. The expectation is that intercompany pairs cancel — the
        residual (often near-zero) is what shows up as the elimination
        adjustment in the consolidated column.
        """
        self.ensure_one()
        elim_ids = set(self.elimination_account_ids.ids)
        eliminations: dict[int, float] = {}
        for r in balance_rows:
            if r["account_id"] in elim_ids:
                eliminations.setdefault(r["account_id"], 0.0)
                eliminations[r["account_id"]] -= r["balance"]
                # Negative — applied as the eliminating entry to bring the
                # consolidated total back to zero (or to the legitimate residual).
        return eliminations

    def build_trial_balance(self, date_from, date_to):
        """Return data structure for the consolidated trial balance."""
        self.ensure_one()
        rows = self._compute_balances(date_from, date_to)
        elims = self._compute_eliminations(rows)

        # Pivot: group rows by account *code* (not id) so that companies' parallel
        # charts merge into a single consolidated line. Each company has its own
        # account.account record, but they share a code (e.g. "11100" exists in
        # both A and B) — consolidation is keyed on that shared code.
        by_account: dict[Any, dict[str, Any]] = {}
        company_ids = self.perimeter_company_ids().ids
        # Map elimination account_id -> code so we can apply the elim against the
        # pivoted (code-keyed) bucket too.
        elim_account_codes: dict[int, Any] = {}
        for r in rows:
            key = r["account_code"] or f"_aid_{r['account_id']}"
            acc = by_account.setdefault(
                key,
                {
                    "account_id": r["account_id"],
                    "account_code": r["account_code"],
                    "account_name": r["account_name"],
                    "account_type": r["account_type"],
                    "by_company": {cid: 0.0 for cid in company_ids},
                    "elimination": 0.0,
                    "consolidated": 0.0,
                },
            )
            acc["by_company"][r["company_id"]] += r["balance"]
            if r["account_id"] in {*self.elimination_account_ids.ids}:
                elim_account_codes[r["account_id"]] = key
        for acc_id, delta in elims.items():
            key = elim_account_codes.get(acc_id)
            if key and key in by_account:
                by_account[key]["elimination"] += delta
        for acc in by_account.values():
            acc["consolidated"] = sum(acc["by_company"].values()) + acc["elimination"]

        # Audit
        self.env["account.consolidation.config"]._audit_report_run(
            "trial_balance", self, date_from, date_to, len(by_account)
        )
        return {
            "config": self,
            "date_from": date_from,
            "date_to": date_to,
            "companies": self.env["res.company"].browse(company_ids),
            "accounts": list(by_account.values()),
        }

    @api.model
    def _audit_report_run(self, kind: str, config, date_from, date_to, row_count: int):
        """Write a row to pdp.audit_log for the report execution."""
        try:
            # The config record itself isn't audited; use a synthetic mixin call
            mixin = self.env["pdp.audited.mixin"]
            mixin._pdp_audit_write(
                f"consolidation_{kind}",
                config.id,
                {
                    "config_name": config.name,
                    "fiscal_year": config.fiscal_year,
                    "date_from": str(date_from),
                    "date_to": str(date_to),
                    "row_count": row_count,
                },
            )
        except Exception:
            # Audit must never block reporting
            pass
