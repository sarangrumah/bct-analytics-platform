# -*- coding: utf-8 -*-
"""Cost budget per division/period with submission consumption check."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Finance Portal document models that consume budget.
CONSUMING_MODELS = (
    "finance.cash.advance",
    "finance.reimbursement",
    "finance.vendor.invoice",
)
# States that "commit" budget (approved onward) vs in-pipeline (submitted).
COMMITTED_STATES = ("approved", "pushed", "posted", "paid")
PIPELINE_STATES = ("submitted",) + COMMITTED_STATES
ENFORCE_PARAM = "custom_finance_budget.enforce"


class FinanceBudget(models.Model):
    _name = "finance.budget"
    _description = "Finance Cost Budget"
    _order = "budget_year desc, division_id"

    name = fields.Char(required=True, default=lambda self: self.env._("New"))
    active = fields.Boolean(default=True)
    division_id = fields.Many2one("finance.vertical", string="Division", required=True)
    cost_center_code = fields.Char(string="Cost Center")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    currency_id = fields.Many2one("res.currency", default=lambda self: self.env.company.currency_id)
    budget_year = fields.Integer(
        string="Budget Year",
        required=True,
        default=lambda self: fields.Date.context_today(self).year,
    )
    budget_amount = fields.Monetary(string="Budget", currency_field="currency_id")
    x_sap_external_id = fields.Char(string="SAP External ID", index=True, copy=False)

    committed_amount = fields.Monetary(
        string="Committed",
        currency_field="currency_id",
        compute="_compute_consumption",
        help="Sum of approved/pushed/posted/paid documents for this division & year.",
    )
    pipeline_amount = fields.Monetary(
        string="In Pipeline",
        currency_field="currency_id",
        compute="_compute_consumption",
        help="Committed plus documents still in approval (submitted).",
    )
    remaining_amount = fields.Monetary(
        string="Remaining",
        currency_field="currency_id",
        compute="_compute_consumption",
    )

    _division_year_company_uniq = models.Constraint(
        "unique(division_id, budget_year, company_id)",
        "A budget already exists for this division/year/company.",
    )
    _sap_external_id_uniq = models.Constraint(
        "unique(x_sap_external_id)",
        "SAP external id must be unique.",
    )

    # ------------------------------------------------------------------
    def _consumed(self, division, year, company, states, exclude=None):
        """Sum committed/pipeline document amounts for a division & year."""
        total = 0.0
        for model in CONSUMING_MODELS:
            Model = self.env.get(model)
            if Model is None:
                continue
            domain = [
                ("division_id", "=", division.id),
                ("state", "in", list(states)),
                ("create_date", ">=", f"{year}-01-01 00:00:00"),
                ("create_date", "<=", f"{year}-12-31 23:59:59"),
            ]
            if company:
                domain.append(("company_id", "=", company.id))
            if exclude and exclude._name == model:
                domain.append(("id", "!=", exclude.id))
            total += sum(Model.sudo().search(domain).mapped("amount"))
        return total

    def _compute_consumption(self):
        for rec in self:
            committed = rec._consumed(rec.division_id, rec.budget_year, rec.company_id, COMMITTED_STATES)
            pipeline = rec._consumed(rec.division_id, rec.budget_year, rec.company_id, PIPELINE_STATES)
            rec.committed_amount = committed
            rec.pipeline_amount = pipeline
            rec.remaining_amount = (rec.budget_amount or 0.0) - committed

    # ------------------------------------------------------------------
    @api.model
    def _check_document_budget(self, document):
        """Raise if ``document`` would overspend its division budget.

        Called from ``finance.document.mixin._finance_validate_submission``.
        No matching budget row → pass (budget not loaded yet). Controlled by the
        ``custom_finance_budget.enforce`` config parameter.
        """
        division = getattr(document, "division_id", False)
        if not division:
            return True
        enforce = self.env["ir.config_parameter"].sudo().get_param(ENFORCE_PARAM, "1")
        if enforce in ("0", "false", "False", ""):
            return True
        year = fields.Date.context_today(document).year
        company = getattr(document, "company_id", False)
        domain = [("division_id", "=", division.id), ("budget_year", "=", year)]
        if company:
            domain.append(("company_id", "=", company.id))
        budget = self.sudo().search(domain, limit=1)
        if not budget:
            return True
        already = budget._consumed(division, year, company, COMMITTED_STATES, exclude=document)
        projected = already + (document.amount or 0.0)
        if projected > (budget.budget_amount or 0.0):
            raise UserError(
                _(
                    "Budget exceeded for division %(div)s (%(year)s): budget %(bud)s, "
                    "already committed %(used)s, this document %(amt)s.",
                    div=division.name,
                    year=year,
                    bud=budget.budget_amount,
                    used=already,
                    amt=document.amount,
                )
            )
        return True
