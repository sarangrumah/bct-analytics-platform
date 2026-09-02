# -*- coding: utf-8 -*-
"""Admin fees on payment registration (tenant-neutral).

Lets a bill/invoice payment carry one or more *admin fee* lines -- each booked
to its own COA -- on top of the amount that settles the bill.

Example: a 1,000,000 vendor bill with a 1,500 bank admin fee produces a
1,001,500 cash-out whose journal entry debits Accounts Payable 1,000,000,
debits the fee account 1,500 and credits the bank 1,001,500::

    Dr  Accounts Payable (counterpart)   1,000,000   -> reconciles the bill
    Dr  Bank Admin Charges (fee COA)         1,500   -> one line per fee COA
    Cr  Bank (liquidity)                             1,001,500

The fees ride Odoo's native payment *write-off* channel, so the counterpart
(``amount - fees``) still equals the bill residual and the bill reconciles in
full. As a bonus the fee lines are ordinary journal items, so they surface on
any payment voucher / receipt without any extra work.
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PaymentRegisterAdminFee(models.TransientModel):
    _name = "payment.register.admin.fee"
    _description = "Admin Fee Line on Payment Registration"

    wizard_id = fields.Many2one("account.payment.register", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="wizard_id.company_id")
    currency_id = fields.Many2one(related="wizard_id.currency_id")
    name = fields.Char(string="Label", default="Admin Fee")
    account_id = fields.Many2one(
        "account.account",
        string="Fee Account",
        required=True,
        domain="[('active', '=', True),"
        " ('account_type', 'not in', ('asset_receivable', 'liability_payable', 'off_balance')),"
        " ('company_ids', 'in', company_id)]",
    )
    amount = fields.Monetary(string="Amount", currency_field="currency_id", required=True)


class AccountPaymentRegister(models.TransientModel):
    _inherit = "account.payment.register"

    admin_fee_line_ids = fields.One2many("payment.register.admin.fee", "wizard_id", string="Admin Fees")
    admin_fee_total = fields.Monetary(
        string="Total Admin Fees",
        currency_field="currency_id",
        compute="_compute_admin_fee_total",
    )

    @api.depends("admin_fee_line_ids.amount")
    def _compute_admin_fee_total(self):
        for wizard in self:
            wizard.admin_fee_total = sum(wizard.admin_fee_line_ids.mapped("amount"))

    @api.onchange("admin_fee_line_ids")
    def _onchange_admin_fee_line_ids(self):
        """Fees ride on top of the bill: cash-out = bill residual + Sum(fees).

        Recomputed from the batch residual on every change, so there is no
        accumulation and removing the fee lines restores the plain amount.

        Adding a fee while several bills are selected also ticks *Group
        Payments*: paying the bills in one transfer is precisely why a single
        admin fee is charged, and the ungrouped path (one payment per bill) has
        nowhere to book it -- see ``_create_payment_vals_from_batch``.
        """
        for wizard in self:
            if not wizard.can_edit_wizard or not wizard.currency_id or not wizard.payment_date:
                continue
            if wizard.admin_fee_line_ids and wizard.can_group_payments and not wizard.group_payment:
                wizard.group_payment = True
            base = wizard._get_total_amounts_to_pay(wizard.batches)["amount_by_default"]
            wizard.amount = base + sum(wizard.admin_fee_line_ids.mapped("amount"))

    # The dedicated multi-COA admin-fee lines replace the native single-account
    # "payment difference" write-off; hide that UI to avoid a confusing double
    # entry (depends mirror the core method's + admin_fee_line_ids).
    @api.depends(
        "early_payment_discount_mode",
        "can_edit_wizard",
        "can_group_payments",
        "group_payment",
        "payment_method_line_id",
        "admin_fee_line_ids",
    )
    def _compute_show_payment_difference(self):
        super()._compute_show_payment_difference()
        for wizard in self:
            if wizard.admin_fee_line_ids:
                wizard.show_payment_difference = False

    # ------------------------------------------------------------------
    # Move generation
    # ------------------------------------------------------------------
    def _prepare_admin_fee_write_off_vals(self):
        """One write-off ``account.move.line`` val per admin fee, own COA each.

        The sign mirrors Odoo's native write-off handling: for an outbound
        payment the fee is a debit (expense/charge); inbound mirrors it.
        """
        self.ensure_one()
        sign = 1 if self.payment_type == "outbound" else -1
        vals = []
        for fee in self.admin_fee_line_ids:
            if not fee.account_id or self.currency_id.is_zero(fee.amount):
                continue
            amount_currency = sign * fee.amount
            vals.append(
                {
                    "name": fee.name or _("Admin Fee"),
                    "account_id": fee.account_id.id,
                    "partner_id": self.partner_id.id,
                    "currency_id": self.currency_id.id,
                    "amount_currency": amount_currency,
                    "balance": self.currency_id._convert(
                        amount_currency,
                        self.company_id.currency_id,
                        self.company_id,
                        self.payment_date,
                    ),
                }
            )
        return vals

    def _create_payment_vals_from_wizard(self, batch_result):
        vals = super()._create_payment_vals_from_wizard(batch_result)
        if self.admin_fee_line_ids:
            self._assert_admin_fee_balance()
            # Replace any native single-line write-off with our per-COA fees.
            vals["write_off_line_vals"] = self._prepare_admin_fee_write_off_vals()
        return vals

    def _create_payment_vals_from_batch(self, batch_result):
        # This path runs for multi-partner / ungrouped registration, where the
        # editable amount + fee lines are not shown, so fees cannot be applied.
        if self.admin_fee_line_ids:
            raise UserError(
                _(
                    "Admin fees are only supported for a single payment. Register "
                    "one bill at a time, or tick 'Group Payments' to combine them."
                )
            )
        return super()._create_payment_vals_from_batch(batch_result)

    def _assert_admin_fee_balance(self):
        """Guard: cash-out must equal bill amount + admin fees.

        ``payment_difference`` is ``(amount settling the bill) - amount``; when
        ``amount == residual + fees`` it equals ``-admin_fee_total``. Any drift
        means the amount was hand-edited out of sync, which would over/under-pay
        the bill -- surface it instead of silently mis-reconciling.
        """
        self.ensure_one()
        drift = self.currency_id.round(self.payment_difference + self.admin_fee_total)
        if not self.currency_id.is_zero(drift):
            raise UserError(
                _(
                    "The payment amount must equal the bill amount plus the admin "
                    "fees (%(fees)s). Set the Amount to %(expected)s.",
                    fees=self.admin_fee_total,
                    expected=self.amount + drift,
                )
            )
