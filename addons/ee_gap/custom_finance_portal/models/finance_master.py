# -*- coding: utf-8 -*-
"""Light master data for the Finance Portal.

Most master data the spreadsheet lists already maps onto stock Odoo models and
is synced from SAP by ``custom_finance_portal_sap`` (COA -> account.account,
Supplier -> res.partner, Item Category -> product.category, Business Plant ->
res.company, Approval Matrix -> approval.matrix, Bank -> res.bank, User/Role ->
res.users). Only the genuinely-missing reference lists live here.

All rows carry ``x_sap_external_id`` so the sync layer can upsert idempotently.
"""

from __future__ import annotations

from odoo import api, fields, models


class FinanceSyncedMixin(models.AbstractModel):
    """Common shape for SAP-synced reference lists."""

    _name = "finance.synced.mixin"
    _description = "Finance Synced Reference Mixin"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(index=True)
    active = fields.Boolean(default=True)
    x_sap_external_id = fields.Char(
        string="SAP External ID",
        index=True,
        copy=False,
        help="Stable key from SAP/Kafka used for idempotent upsert.",
    )

    _sap_external_id_uniq = models.Constraint(
        "unique(x_sap_external_id)",
        "SAP external id must be unique per reference list.",
    )


class FinanceVertical(models.Model):
    _name = "finance.vertical"
    _inherit = ["finance.synced.mixin"]
    _description = "Finance Vertical / Division"
    _order = "name"

    cost_center_code = fields.Char(string="Cost Center Code")
    pic_finance_user_id = fields.Many2one(
        "res.users",
        string="PIC Finance",
        help="Finance Approval Matrix: PIC Finance mapped for this vertical.",
    )


class FinanceSubmissionType(models.Model):
    _name = "finance.submission.type"
    _inherit = ["finance.synced.mixin"]
    _description = "Finance Submission Type"
    _order = "name"

    category = fields.Selection(
        selection=[
            ("perjadin", "Perjalanan Dinas"),
            ("non_perjadin", "Non Perjadin"),
            ("reimbursement", "Reimbursement & Expenses"),
            ("cash_advance", "Cash Advance"),
            ("vendor_invoice", "Vendor Invoice"),
            ("other", "Other"),
        ],
        default="other",
        required=True,
    )


class FinanceInvoiceType(models.Model):
    _name = "finance.invoice.type"
    _inherit = ["finance.synced.mixin"]
    _description = "Finance Invoice Type (PO / Non-PO)"
    _order = "name"

    is_po_based = fields.Boolean(
        string="PO Based",
        help="True = Invoice Vendor PO Non-Trade, False = Non-PO Non-Trade.",
    )


class FinanceInvoiceRoutineType(models.Model):
    _name = "finance.invoice.routine.type"
    _inherit = ["finance.synced.mixin"]
    _description = "Finance Invoice Routine Type"
    _order = "name"

    is_routine = fields.Boolean(
        string="Routine",
        help="Routine transactions submit on a monthly basis (e.g. internet provider, land loan).",
    )


class FinanceItemSubmission(models.Model):
    _name = "finance.item.submission"
    _inherit = ["finance.synced.mixin"]
    _description = "Finance Item of Submission"
    _order = "name"

    product_category_id = fields.Many2one(
        "product.category",
        string="Item Category",
        help="Maps to SAP item category (Trip / Meal / Hotel allowance, ...).",
    )


class FinanceLimitation(models.Model):
    _name = "finance.limitation"
    _inherit = ["finance.synced.mixin"]
    _description = "Finance Limitation for Submission"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    submission_type_id = fields.Many2one("finance.submission.type", string="Applies To Submission Type")
    division_id = fields.Many2one("finance.vertical", string="Applies To Division")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )
    pr_required_above = fields.Monetary(
        string="PR Required Above",
        currency_field="currency_id",
        help="When a submission amount exceeds this, a PR number is mandatory. Defaults to Rp 1.000.000 when unset.",
    )
    max_amount = fields.Monetary(
        string="Max Submission Amount",
        currency_field="currency_id",
        help="Hard ceiling per submission (0 = no ceiling).",
    )

    @api.model
    def _resolve_for(self, document):
        """Best-match limitation for a finance document.

        Prefers a row scoped to the document's submission type + division, then
        falls back to broader rows. Returns an empty recordset when none match.
        """
        sub_type = getattr(document, "submission_type_id", False)
        division = getattr(document, "division_id", False)
        company = getattr(document, "company_id", False)
        domain = [("active", "=", True)]
        if company:
            domain += ["|", ("company_id", "=", company.id), ("company_id", "=", False)]
        candidates = self.sudo().search(domain, order="sequence, id")

        def _score(rec):
            score = 0
            if sub_type and rec.submission_type_id == sub_type:
                score += 2
            elif rec.submission_type_id:
                return -1  # scoped to a different submission type → disqualify
            if division and rec.division_id == division:
                score += 1
            elif rec.division_id:
                return -1
            return score

        best = False
        best_score = -1
        for rec in candidates:
            s = _score(rec)
            if s > best_score:
                best, best_score = rec, s
        return best or self.browse()
