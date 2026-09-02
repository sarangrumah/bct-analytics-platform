# -*- coding: utf-8 -*-
"""Payment Voucher printout helpers on ``account.payment``.

The Payment Voucher renders a vendor (outbound) payment as an accounting
voucher: a meta header (voucher no + source bank), the payment's own journal
entry rendered as a COA / DEBIT / CREDIT table (with the reconciled AP document
and vendor invoice reference per line), the amount-in-words, and the recipient
(vendor) bank block. The same body serves the inbound Payment Receipt.
"""

from __future__ import annotations

from odoo import api, fields, models

from .terbilang import terbilang_id

_INVOICE_TYPES = ("in_invoice", "in_refund", "out_invoice", "out_refund")


class AccountPayment(models.Model):
    _inherit = "account.payment"

    # ------------------------------------------------------------------
    # Extra payment dimensions (user-testing feedback: Payment #4)
    # ------------------------------------------------------------------
    # Native fields cover Memo (``memo``), Reference (``payment_reference``) and
    # Destination Account (``destination_account_id`` — already store/readonly=False).
    # The following are added on top. The two account overrides change the POSTED
    # GL; Note / Remark are informational (stored, shown on the voucher).
    l10n_ou_analytic_id = fields.Many2one(
        "account.analytic.account",
        string="Operating Unit",
        copy=False,
        domain="[('plan_id.name', '=', 'Operating Unit')]",
        help="Head Office / Store this payment belongs to. Stamped on the "
        "payment's journal lines for per-OU P&L reporting.",
    )
    l10n_override_outstanding_account_id = fields.Many2one(
        "account.account",
        string="Override Outstanding Account",
        copy=False,
        check_company=True,
        help="When set, overrides the outstanding (liquidity) account on the "
        "posted payment instead of the one from the payment method line.",
    )
    l10n_note = fields.Char(string="Note", copy=False)
    l10n_remark = fields.Char(string="Remark", copy=False)

    # -- Outstanding-account override (impacts the posted GL) --------------
    # Re-declaring @api.depends REPLACES the inherited set, so the base trigger
    # (payment_method_line_id) is relisted alongside the override field.
    @api.depends("payment_method_line_id", "l10n_override_outstanding_account_id")
    def _compute_outstanding_account_id(self):
        super()._compute_outstanding_account_id()
        for pay in self:
            if pay.l10n_override_outstanding_account_id:
                pay.outstanding_account_id = pay.l10n_override_outstanding_account_id.id

    # -- Operating-Unit stamping on the payment's journal lines -----------
    def _retail_stamp_ou(self, line_vals_list):
        """Merge the payment's OU analytic into each line's distribution."""
        self.ensure_one()
        if not self.l10n_ou_analytic_id:
            return line_vals_list
        POL = self.env["purchase.order.line"]
        for vals in line_vals_list:
            vals["analytic_distribution"] = POL._retail_merge_ou_distribution(
                vals.get("analytic_distribution"), self.l10n_ou_analytic_id.id
            )
        return line_vals_list

    def _prepare_move_liquidity_lines(self, default_values):
        return self._retail_stamp_ou(super()._prepare_move_liquidity_lines(default_values))

    def _prepare_move_counterpart_lines(self, default_values):
        return self._retail_stamp_ou(super()._prepare_move_counterpart_lines(default_values))

    # ------------------------------------------------------------------
    # Source document (reconciled bill/invoice) behind a journal line
    # ------------------------------------------------------------------
    def _edo_line_source_doc(self, line):
        """The invoice/bill this line is reconciled against, if any."""
        counterparts = line.matched_debit_ids.mapped("credit_move_id.move_id")
        counterparts |= line.matched_credit_ids.mapped("debit_move_id.move_id")
        bills = counterparts.filtered(lambda m: m.move_type in _INVOICE_TYPES)
        return bills[:1]

    def _edo_voucher_rows(self):
        """One dict per journal item of the payment's move — the voucher table."""
        self.ensure_one()
        rows = []
        move = self.move_id
        if not move:
            return rows
        lines = move.line_ids.filtered(lambda l: l.display_type not in ("line_section", "line_note"))
        for line in lines:
            src = self._edo_line_source_doc(line)
            orig = abs(line.amount_currency) if line.amount_currency else abs(line.balance)
            rate = 1.0
            if line.amount_currency and line.balance:
                rate = abs(line.balance) / abs(line.amount_currency)
            rows.append(
                {
                    "coa": line.account_id.name or line.name or "",
                    "desc": line.name or "",
                    "doc_ap": (src.name if src else self.name) or "",
                    "ref_vendor": (src.ref if src else (self.payment_reference or self.memo or "")) or "",
                    "vendor": (line.partner_id or self.partner_id).name or "",
                    "currency": self.currency_id.name or "",
                    "orig_amount": orig,
                    "rate": rate,
                    "debit": line.debit,
                    "credit": line.credit,
                }
            )
        return rows

    # ------------------------------------------------------------------
    # Recipient (vendor) bank block
    # ------------------------------------------------------------------
    def _edo_recipient_bank(self):
        self.ensure_one()
        bank = self.partner_bank_id
        return {
            "bank_name": bank.bank_id.name if bank else "",
            "acc_number": bank.acc_number if bank else "",
            "acc_holder": (bank.acc_holder_name or (bank.partner_id.name if bank else "")) if bank else "",
            "branch": (getattr(bank.bank_id, "street", "") or "") if bank else "",
            "swift": (bank.bank_id.bic or "") if bank else "",
        }

    def _edo_source_bank_account(self):
        """The company bank account funding the payment (header 'Account No.')."""
        self.ensure_one()
        acc = self.journal_id.bank_account_id
        if not acc:
            return ""
        parts = [acc.acc_number]
        if acc.bank_id:
            parts.append(acc.bank_id.name)
        return " - ".join(p for p in parts if p)

    # ------------------------------------------------------------------
    # Amount in words
    # ------------------------------------------------------------------
    def _edo_amount_in_words(self):
        self.ensure_one()
        suffix = "Rupiah" if self.currency_id.name == "IDR" else (self.currency_id.currency_unit_label or "")
        return terbilang_id(self.amount, suffix=suffix)
