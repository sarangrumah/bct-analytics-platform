# -*- coding: utf-8 -*-
"""Customer credit-limit enforcement on sale order confirmation."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ResPartnerCreditLimit(models.Model):
    _inherit = "res.partner"

    custom_credit_limit = fields.Monetary(
        string="Credit Limit (Custom)",
        currency_field="currency_id",
        help="Maximum outstanding receivable amount allowed. Mirrors and "
        "supersedes the base account ``credit_limit`` for partners "
        "subject to the custom credit check.",
    )
    custom_credit_limit_check_method = fields.Selection(
        [
            ("none", "No Check"),
            ("warning", "Warning Only"),
            ("block", "Block Confirmation"),
        ],
        default="warning",
        string="Credit Check Method",
        help="Behaviour when a sale.order exceeds the credit limit. "
        "Defaults to 'warning'. Left nullable so partners created via "
        "indirect chains (res.users -> hr.employee -> partner) don't "
        "trip the NOT NULL constraint before the ORM default fires.",
    )
    custom_outstanding_amount = fields.Monetary(
        compute="_compute_custom_credit_metrics",
        currency_field="currency_id",
        string="Outstanding (Custom)",
    )
    custom_credit_available = fields.Monetary(
        compute="_compute_custom_credit_metrics",
        currency_field="currency_id",
        string="Available Credit",
    )

    # Spec-aligned aliases (related fields)
    credit_limit_check_method = fields.Selection(
        related="custom_credit_limit_check_method",
        readonly=False,
        store=True,
        string="Credit Check Method (Spec)",
    )
    outstanding_amount = fields.Monetary(
        compute="_compute_custom_credit_metrics",
        currency_field="currency_id",
        string="Outstanding (Spec)",
    )
    available_credit = fields.Monetary(
        compute="_compute_custom_credit_metrics",
        currency_field="currency_id",
        string="Available Credit (Spec)",
    )

    @api.depends("custom_credit_limit")
    def _compute_custom_credit_metrics(self):
        Move = self.env["account.move"]
        for partner in self:
            invoices = Move.search(
                [
                    ("partner_id", "=", partner.id),
                    ("move_type", "in", ("out_invoice", "out_refund")),
                    ("state", "=", "posted"),
                    ("payment_state", "in", ("not_paid", "partial")),
                ]
            )
            outstanding = sum(invoices.mapped("amount_residual"))
            partner.custom_outstanding_amount = outstanding
            partner.custom_credit_available = max(
                (partner.custom_credit_limit or 0.0) - outstanding,
                0.0,
            )
            partner.outstanding_amount = outstanding
            partner.available_credit = (partner.custom_credit_limit or 0.0) - outstanding


class CreditCheckLog(models.Model):
    _name = "custom.credit.check.log"
    _description = "Credit Check Log"
    _inherit = ["pdp.audited.mixin"]
    _order = "check_date desc, id desc"

    partner_id = fields.Many2one("res.partner", required=True, index=True)
    sale_order_id = fields.Many2one("sale.order", index=True)
    check_date = fields.Datetime(default=fields.Datetime.now, readonly=True)
    checked_by = fields.Many2one("res.users", readonly=True)
    limit_at_check = fields.Float()
    outstanding_at_check = fields.Float()
    order_amount = fields.Float()
    decision = fields.Selection(
        [
            ("pass", "Pass"),
            ("allowed", "Allowed"),
            ("warn", "Warn"),
            ("warned", "Warned"),
            ("blocked", "Blocked"),
        ],
        readonly=True,
    )
    note = fields.Text()
    # Spec-aligned aliases
    checked_by_id = fields.Many2one(related="checked_by", store=False, string="Checked By (Spec)")
    reason = fields.Text(related="note", readonly=False, string="Note (Spec)")

    def _pdp_audit_classification(self):
        return "financial"


class SaleOrderCreditCheck(models.Model):
    _inherit = "sale.order"

    custom_credit_check_log_id = fields.Many2one(
        "custom.credit.check.log",
        string="Credit Check Log",
        readonly=True,
        copy=False,
    )

    def action_confirm(self):
        for order in self:
            order._check_credit_limit()
        return super().action_confirm()

    def _check_credit_limit(self):
        """Spec-named entry-point — delegates to _custom_credit_check."""
        self.ensure_one()
        return self._custom_credit_check()

    def _custom_credit_check(self):
        self.ensure_one()
        partner = self.partner_id
        method = partner.custom_credit_limit_check_method or "none"
        limit = partner.custom_credit_limit or 0.0
        outstanding = partner.custom_outstanding_amount
        order_total = self.amount_total
        projected = outstanding + order_total
        decision = "pass"
        if method == "none" or not limit:
            return
        if projected > limit:
            decision = "blocked" if method == "block" else "warn"
        log_vals = {
            "partner_id": partner.id,
            "sale_order_id": self.id,
            "limit_at_check": limit,
            "outstanding_at_check": outstanding,
            "order_amount": order_total,
            "decision": decision,
            "checked_by": self.env.user.id,
            "note": (
                f"Limit: {limit:.2f}, Outstanding: {outstanding:.2f}, "
                f"Order: {order_total:.2f}, Projected: {projected:.2f}"
            ),
        }
        # NOTE: persisting the audit row on the same cursor means a downstream
        # rollback (caller catches UserError, or framework rolls back) will
        # erase the log too. For Go-Live the spec-required behaviour is "audit
        # persists even on block" — that needs a separate cursor + commit, but
        # FK-referenced records (partner, sale.order) must already be
        # committed in the outer transaction. Track as Go-Live hardening.
        log = self.env["custom.credit.check.log"].sudo().create(log_vals)
        self.custom_credit_check_log_id = log.id
        if decision == "blocked":
            raise UserError(
                _(
                    "Credit limit exceeded for %(p)s: outstanding %(o).2f + "
                    "this order %(t).2f would exceed limit %(l).2f.",
                    p=partner.display_name,
                    o=outstanding,
                    t=order_total,
                    l=limit,
                )
            )
        if decision == "warn":
            self.message_post(
                body=_(
                    "Credit warning: projected total %(pj).2f exceeds limit %(l).2f.",
                    pj=projected,
                    l=limit,
                )
            )
