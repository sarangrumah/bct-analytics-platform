# -*- coding: utf-8 -*-
"""Admin fees on payment registration.

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
the Payment Voucher / Receipt without any extra work.
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class RetailPaymentRegisterFee(models.TransientModel):
    _name = "retail.payment.register.fee"
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
    is_mdr = fields.Boolean(
        string="MDR",
        help="Auto-generated card MDR deduction (negative amount = netted off "
        "the settlement). Recomputed from the Card BIN, not entered by hand.",
    )


class AccountPaymentRegister(models.TransientModel):
    _inherit = "account.payment.register"

    admin_fee_line_ids = fields.One2many("retail.payment.register.fee", "wizard_id", string="Admin Fees")
    admin_fee_total = fields.Monetary(
        string="Total Admin Fees",
        currency_field="currency_id",
        compute="_compute_admin_fee_total",
    )
    # --- Card MDR (Merchant Discount Rate) -----------------------------------
    # On an inbound (customer) card receipt the acquirer settles NET of the MDR
    # fee. The MDR rides the admin-fee channel as a NEGATIVE fee line, so the
    # cash-in nets down to the settlement while the receivable still reconciles
    # in full:  Dr Bank (net) / Dr MDR expense / Cr Receivable.
    x_card_bin = fields.Char(
        string="Card BIN",
        help="Leading digits of the customer's card. Resolves the acquirer and "
        "MDR rate; the MDR fee is then netted off this receipt.",
    )
    x_mdr_bin_id = fields.Many2one("retail.mdr.bin", string="MDR Mapping", readonly=True, copy=False)

    # --- Extra payment dimensions (Payment #4) -------------------------------
    # Propagated to the created account.payment. Memo is the native
    # ``communication`` field; OU + override-outstanding change the posted GL,
    # Note / Remark are informational.
    l10n_ou_analytic_id = fields.Many2one(
        "account.analytic.account",
        string="Operating Unit",
        domain="[('plan_id.name', '=', 'Operating Unit')]",
        help="Head Office / Store this payment belongs to; stamped on the payment's journal lines.",
    )
    l10n_override_outstanding_account_id = fields.Many2one(
        "account.account",
        string="Override Outstanding Account",
        check_company=True,
        help="Overrides the outstanding (liquidity) account on the posted payment.",
    )
    l10n_note = fields.Char(string="Note")
    l10n_remark = fields.Char(string="Remark")

    def _retail_payment_extra_vals(self):
        """Extra account.payment vals carried from the wizard."""
        self.ensure_one()
        vals = {}
        if self.l10n_ou_analytic_id:
            vals["l10n_ou_analytic_id"] = self.l10n_ou_analytic_id.id
        if self.l10n_override_outstanding_account_id:
            vals["l10n_override_outstanding_account_id"] = self.l10n_override_outstanding_account_id.id
        if self.l10n_note:
            vals["l10n_note"] = self.l10n_note
        if self.l10n_remark:
            vals["l10n_remark"] = self.l10n_remark
        return vals

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

    @api.onchange("x_card_bin")
    def _onchange_x_card_bin(self):
        """Resolve the card BIN and net the MDR fee off this receipt.

        MDR only applies to inbound (customer) card receipts. The fee is added as
        a NEGATIVE ``is_mdr`` admin-fee line so the shared machinery nets the
        cash-in and books the MDR to its own account. Re-running replaces the
        previous MDR line; clearing the BIN removes it.
        """
        for wizard in self:
            # Drop any prior auto-generated MDR line first (idempotent).
            mdr_lines = wizard.admin_fee_line_ids.filtered("is_mdr")
            if mdr_lines:
                wizard.admin_fee_line_ids -= mdr_lines
            wizard.x_mdr_bin_id = False
            if not wizard.x_card_bin or wizard.payment_type != "inbound":
                self._recompute_amount_with_fees(wizard)
                continue
            if not wizard.can_edit_wizard or not wizard.currency_id or not wizard.payment_date:
                continue
            mapping = self.env["retail.mdr.bin"]._match_bin(
                wizard.x_card_bin, date=wizard.payment_date, company=wizard.company_id
            )
            if not mapping or not mapping.mdr_account_id:
                self._recompute_amount_with_fees(wizard)
                continue
            base = wizard._get_total_amounts_to_pay(wizard.batches)["amount_by_default"]
            mdr = wizard.currency_id.round(mapping._compute_mdr(base))
            wizard.x_mdr_bin_id = mapping.id
            if not wizard.currency_id.is_zero(mdr):
                label = _(
                    "MDR %(pct)s%% - %(bank)s",
                    pct=mapping.mdr_percent,
                    bank=mapping.acquirer_bank_id.name or mapping.name,
                )
                wizard.admin_fee_line_ids = [
                    (
                        0,
                        0,
                        {
                            "name": label,
                            "account_id": mapping.mdr_account_id.id,
                            "amount": -mdr,  # negative: netted off the settlement
                            "is_mdr": True,
                        },
                    )
                ]
            self._recompute_amount_with_fees(wizard)

    @staticmethod
    def _recompute_amount_with_fees(wizard):
        if not wizard.can_edit_wizard or not wizard.currency_id or not wizard.payment_date:
            return
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
            line_vals = {
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
            # Admin fees are the P&L side of the payment, so carry the OU there
            # too for per-OU reporting.
            if self.l10n_ou_analytic_id:
                line_vals["analytic_distribution"] = self.env["purchase.order.line"]._retail_merge_ou_distribution(
                    None, self.l10n_ou_analytic_id.id
                )
            vals.append(line_vals)
        return vals

    def _create_payment_vals_from_wizard(self, batch_result):
        vals = super()._create_payment_vals_from_wizard(batch_result)
        if self.admin_fee_line_ids:
            self._assert_admin_fee_balance()
            # Replace any native single-line write-off with our per-COA fees.
            vals["write_off_line_vals"] = self._prepare_admin_fee_write_off_vals()
        vals.update(self._retail_payment_extra_vals())
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
        vals = super()._create_payment_vals_from_batch(batch_result)
        vals.update(self._retail_payment_extra_vals())
        return vals

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
