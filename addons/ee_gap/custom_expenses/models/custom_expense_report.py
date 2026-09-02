# -*- coding: utf-8 -*-
"""Expense report — bulk submit / approve / register payment for hr.expense."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CustomExpenseReport(models.Model):
    _name = "custom.expense.report"
    _description = "Expense Report"
    _inherit = ["mail.thread", "approval.mixin"]
    _order = "create_date desc"

    name = fields.Char(
        string="Reference",
        required=True,
        default=lambda self: self.env._("New"),
        tracking=True,
        copy=False,
    )
    employee_id = fields.Many2one(
        "hr.employee",
        string="Employee",
        required=True,
        tracking=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    expense_ids = fields.Many2many(
        "hr.expense",
        "custom_expense_report_expense_rel",
        "report_id",
        "expense_id",
        string="Expenses",
        domain="[('employee_id', '=', employee_id)]",
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("submitted", "Submitted"),
            ("approved", "Approved"),
            ("paid", "Paid"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        tracking=True,
        required=True,
        copy=False,
    )
    total_amount = fields.Monetary(
        string="Total",
        currency_field="currency_id",
        compute="_compute_total_amount",
        store=True,
    )
    expense_count = fields.Integer(
        string="# Expenses",
        compute="_compute_total_amount",
        store=True,
    )
    payment_ids = fields.Many2many(
        "account.payment",
        "custom_expense_report_payment_rel",
        "report_id",
        "payment_id",
        string="Payments",
        copy=False,
    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends("expense_ids", "expense_ids.total_amount")
    def _compute_total_amount(self):
        for rep in self:
            rep.total_amount = sum(rep.expense_ids.mapped("total_amount"))
            rep.expense_count = len(rep.expense_ids)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("custom.expense.report") or _("New")
        return super().create(vals_list)

    @api.constrains("expense_ids", "employee_id")
    def _check_expenses_same_employee(self):
        for rep in self:
            for exp in rep.expense_ids:
                if exp.employee_id and exp.employee_id != rep.employee_id:
                    raise UserError(
                        _(
                            "Expense '%(name)s' belongs to %(emp)s, not %(report_emp)s.",
                            name=exp.name or "?",
                            emp=exp.employee_id.name,
                            report_emp=rep.employee_id.name,
                        )
                    )

    # ------------------------------------------------------------------
    # Workflow actions
    # ------------------------------------------------------------------
    def action_submit_for_approval(self):
        """Move to submitted + auto-submit approval via the generic matrix."""
        for rep in self:
            if rep.state != "draft":
                raise UserError(_("Only draft reports can be submitted."))
            if not rep.expense_ids:
                raise UserError(_("Add at least one expense before submitting."))
            rep.state = "submitted"
            # Auto-create + submit the approval request when a matrix matches
            # (no-op when none). The report stays "submitted" until the engine
            # grants approval, which then calls _approval_on_granted below.
            rep._approval_request_or_proceed()
        return True

    def _approval_on_granted(self):
        """Engine hook: advance the report to approved once all tiers pass."""
        return self.action_approve()

    def action_approve(self):
        """Mark approved — gated by the matrix when one matches."""
        for rep in self:
            if rep.state not in ("submitted", "draft"):
                raise UserError(_("Only submitted reports can be approved."))
            rep._approval_check_required()
            rep.state = "approved"
        return True

    def action_cancel(self):
        for rep in self:
            if rep.state == "paid":
                raise UserError(_("Cannot cancel a paid report."))
            rep.state = "cancelled"
            rep.action_cancel_approval()
        return True

    def action_reset_to_draft(self):
        for rep in self:
            if rep.state == "paid":
                raise UserError(_("Cannot reset a paid report."))
            rep.state = "draft"
        return True

    def action_register_payment(self):
        """Open the standard register-payment wizard scoped to this report.

        When the wizard is unavailable (no account.payment.register, e.g.
        in test envs), creates a single aggregate ``account.payment`` per
        report.
        """
        self.ensure_one()
        if self.state != "approved":
            raise UserError(_("Approve the report before registering a payment."))
        if not self.expense_ids:
            raise UserError(_("Nothing to pay."))

        partner = False
        if self.employee_id and getattr(self.employee_id, "work_contact_id", False):
            partner = self.employee_id.work_contact_id
        if not partner:
            raise UserError(_("Employee %s has no work contact for payment.") % self.employee_id.name)

        journal = (
            self.env["account.journal"]
            .sudo()
            .search(
                [("type", "in", ("bank", "cash")), ("company_id", "=", self.company_id.id)],
                limit=1,
            )
        )

        # Only reimburse own_account / non-corporate-card expenses
        reimbursable = self.expense_ids.filtered(
            lambda e: not e.x_corporate_card_id and getattr(e, "payment_mode", "own_account") != "company_account"
        )
        if not reimbursable:
            self.state = "paid"
            self.message_post(
                body=_("All expenses paid via corporate card — nothing to reimburse."),
                subtype_xmlid="mail.mt_note",
            )
            return True

        amount = sum(reimbursable.mapped("total_amount"))
        Payment = self.env["account.payment"].sudo()
        payment = Payment.create(
            {
                "payment_type": "outbound",
                "partner_type": "supplier",
                "partner_id": partner.id,
                "amount": float(amount or 0.0),
                "currency_id": self.currency_id.id,
                "journal_id": journal.id if journal else False,
                "ref": _("Expense Report: %s") % self.name,
                "memo": _("Expense Report: %s") % self.name,
            }
        )
        self.payment_ids = [(4, payment.id)]
        self.state = "paid"
        self.message_post(
            body=_("Payment %(pid)s registered for %(amt)s.")
            % {
                "pid": payment.id,
                "amt": amount,
            },
            subtype_xmlid="mail.mt_note",
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.payment",
            "res_id": payment.id,
            "view_mode": "form",
        }
