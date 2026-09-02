# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Optional back-reference columns added by downstream modules
# (custom_ppob_sale -> ppob_transaction_id, custom_ppob_va -> va_topup_id).
# Guarded against ``_fields`` so the wallet installs and operates cleanly
# before those modules are present.
_OPTIONAL_LINK_KEYS = ("ppob_transaction_id", "va_topup_id")


class PpobWallet(models.Model):
    _name = "custom.ppob.wallet"
    _description = "PPOB Mitra Wallet"
    _order = "partner_id, class_id"
    _rec_name = "display_name"

    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Mitra",
        required=True,
        ondelete="restrict",
        domain=[("x_custom_ppob_is_mitra", "=", True)],
    )
    class_id = fields.Many2one(
        comodel_name="custom.ppob.product.class",
        string="Class",
        required=True,
        ondelete="restrict",
    )
    balance = fields.Monetary(
        currency_field="currency_id",
        readonly=True,
        copy=False,
        default=0.0,
        help="Current wallet balance. Updated atomically by _atomic_debit and _atomic_credit; do NOT assign directly.",
    )
    credit_limit = fields.Monetary(
        currency_field="currency_id",
        default=0.0,
        help="Amount the mitra is allowed to overdraw (credit line). "
        "balance + credit_limit is the hard ceiling for debits.",
    )
    state = fields.Selection(
        selection=[
            ("active", "Active"),
            ("frozen", "Frozen"),
        ],
        default="active",
        required=True,
    )
    account_id = fields.Many2one(
        comodel_name="account.account",
        string="Liability Account",
        required=True,
        domain="[('account_type', 'in', ['liability_current', 'liability_payable'])]",
        help="GL liability account that the wallet posts against. Usually derived from the product class default.",
    )
    journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Wallet Journal",
        required=True,
        domain="[('type', '=', 'general')]",
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        default=lambda self: self.env.company,
        required=True,
    )
    move_ids = fields.One2many(
        comodel_name="custom.ppob.wallet.move",
        inverse_name="wallet_id",
        string="Moves",
    )
    display_name = fields.Char(compute="_compute_display_name", store=True)

    _partner_class_uniq = models.Constraint(
        "unique(partner_id, class_id, company_id)",
        "Each mitra can have only one wallet per product class per company.",
    )

    @api.depends("partner_id.display_name", "class_id.code")
    def _compute_display_name(self):
        for w in self:
            partner = w.partner_id.display_name or "?"
            klass = w.class_id.code or "?"
            w.display_name = f"{partner} / {klass}"

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("account_id") and vals.get("class_id"):
                klass = self.env["custom.ppob.product.class"].browse(vals["class_id"])
                if klass.default_wallet_account_id:
                    vals["account_id"] = klass.default_wallet_account_id.id
            if not vals.get("journal_id"):
                journal = self.env.ref(
                    "custom_ppob_wallet.journal_ppob_wallet",
                    raise_if_not_found=False,
                )
                if journal:
                    vals["journal_id"] = journal.id
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Atomic debit / credit primitives
    # ------------------------------------------------------------------

    def _lock(self):
        """Acquire a row-level lock on this wallet for the duration of the
        PostgreSQL transaction. Concurrent _atomic_* calls on the same wallet
        row serialize here. Returns the fresh balance read under lock."""
        self.ensure_one()
        self.env.cr.execute(
            "SELECT id, balance FROM custom_ppob_wallet WHERE id = %s FOR UPDATE",
            (self.id,),
        )
        row = self.env.cr.fetchone()
        if not row:
            raise UserError(_("Wallet %s not found.") % self.id)
        return row[1] or 0.0  # coalesce legacy NULL balances

    @api.model
    def _build_move_vals(self, base_vals, extras):
        """Merge optional downstream link keys into a wallet.move vals dict,
        including a link key only when the column actually exists."""
        WalletMove = self.env["custom.ppob.wallet.move"]
        for key in _OPTIONAL_LINK_KEYS:
            if key in WalletMove._fields and extras.get(key) is not None:
                base_vals[key] = extras[key]
        return base_vals

    def _post_wallet_journal(self, amount, move_type, reason, counterpart_account, partner=None):
        """Post an account.move for a wallet movement.

        For credit_wallet (topup, positive into wallet):
            Dr counterpart (Cash BCA Escrow, Rebate Expense, ...)
            Cr wallet liability account

        For debit_wallet (wallet pays out):
            Dr wallet liability account
            Cr counterpart (Sales PPOB, Cash, ...)
        """
        self.ensure_one()
        amount = abs(amount)
        partner = partner or self.partner_id
        if move_type == "credit_wallet":
            debit_account = counterpart_account
            credit_account = self.account_id
        elif move_type == "debit_wallet":
            debit_account = self.account_id
            credit_account = counterpart_account
        else:
            raise UserError(_("Unknown wallet journal move type: %s") % move_type)

        move = self.env["account.move"].create(
            {
                "journal_id": self.journal_id.id,
                "move_type": "entry",
                "ref": reason,
                "partner_id": partner.id,
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "account_id": debit_account.id,
                            "partner_id": partner.id,
                            "name": reason,
                            "debit": amount,
                            "credit": 0.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "account_id": credit_account.id,
                            "partner_id": partner.id,
                            "name": reason,
                            "debit": 0.0,
                            "credit": amount,
                        },
                    ),
                ],
            }
        )
        move.action_post()
        return move

    def _atomic_debit(self, amount, reason, counterpart_account, **extras):
        """Atomically debit the wallet by ``amount`` (positive).

        Raises UserError if state != active or if balance + credit_limit is
        insufficient. Returns the created custom.ppob.wallet.move.

        Optional ``ppob_transaction_id`` / ``va_topup_id`` kwargs link the move
        back to the originating record when those columns exist.
        """
        self.ensure_one()
        if amount <= 0:
            raise UserError(_("Debit amount must be positive."))
        if self.state != "active":
            raise UserError(_("Wallet %s is frozen.") % self.display_name)

        fresh_balance = self._lock()
        ceiling = fresh_balance + (self.credit_limit or 0.0)
        if amount > ceiling:
            raise UserError(
                _(
                    "Insufficient wallet balance for %(mitra)s [%(klass)s]. "
                    "Requested: %(req).2f. Available (balance+limit): %(avail).2f."
                )
                % {
                    "mitra": self.partner_id.display_name,
                    "klass": self.class_id.code,
                    "req": amount,
                    "avail": ceiling,
                }
            )

        move = self._post_wallet_journal(
            amount=amount,
            move_type="debit_wallet",
            reason=reason,
            counterpart_account=counterpart_account,
        )
        new_balance = fresh_balance - amount
        move_vals = self._build_move_vals(
            {
                "wallet_id": self.id,
                "type": extras.get("move_type") or "sale",
                "amount_signed": -amount,
                "balance_after": new_balance,
                "ref": reason,
                "move_id": move.id,
                "state": "posted",
            },
            extras,
        )
        wallet_move = self.env["custom.ppob.wallet.move"].create(move_vals)
        self.env.cr.execute(
            "UPDATE custom_ppob_wallet SET balance = %s WHERE id = %s",
            (new_balance, self.id),
        )
        self.invalidate_recordset(["balance"])
        return wallet_move

    def _atomic_credit(self, amount, reason, counterpart_account, **extras):
        """Atomically credit the wallet by ``amount`` (positive)."""
        self.ensure_one()
        if amount <= 0:
            raise UserError(_("Credit amount must be positive."))
        if self.state != "active":
            raise UserError(_("Wallet %s is frozen.") % self.display_name)

        fresh_balance = self._lock()
        move = self._post_wallet_journal(
            amount=amount,
            move_type="credit_wallet",
            reason=reason,
            counterpart_account=counterpart_account,
        )
        new_balance = fresh_balance + amount
        move_vals = self._build_move_vals(
            {
                "wallet_id": self.id,
                "type": extras.get("move_type") or "topup",
                "amount_signed": amount,
                "balance_after": new_balance,
                "ref": reason,
                "move_id": move.id,
                "state": "posted",
            },
            extras,
        )
        wallet_move = self.env["custom.ppob.wallet.move"].create(move_vals)
        self.env.cr.execute(
            "UPDATE custom_ppob_wallet SET balance = %s WHERE id = %s",
            (new_balance, self.id),
        )
        self.invalidate_recordset(["balance"])
        return wallet_move

    def _atomic_credit_with_tax(self, gross_amount, output_tax, reason, counterpart_account, **extras):
        """Tax-inclusive sister of ``_atomic_credit``.

        Splits ``gross_amount`` using ``output_tax`` (a tax-inclusive sale tax
        record), grows the wallet by DPP only, and routes PPN to the tax's
        repartition account (typically PPN Keluaran).

        Posts a 3-line entry:
            Dr counterpart_account                     gross_amount
                Cr wallet.account_id                   DPP
                Cr ppn_account (from tax repartition)  PPN
        """
        self.ensure_one()
        if gross_amount <= 0:
            raise UserError(_("Credit amount must be positive."))
        if self.state != "active":
            raise UserError(_("Wallet %s is frozen.") % self.display_name)
        if not output_tax:
            return self._atomic_credit(gross_amount, reason, counterpart_account, **extras)

        # DPP / PPN split using the tax record (price-included rate).
        rate = (output_tax.amount or 0.0) / 100.0
        if rate <= 0:
            return self._atomic_credit(gross_amount, reason, counterpart_account, **extras)
        dpp = round(gross_amount / (1.0 + rate), 2)
        ppn = round(gross_amount - dpp, 2)

        # Resolve PPN account from tax repartition (invoice / tax line).
        tax_lines = output_tax.invoice_repartition_line_ids.filtered(
            lambda l: l.repartition_type == "tax" and l.account_id
        )
        if not tax_lines:
            raise UserError(
                _("Output tax %s has no repartition line with an account_id; cannot route the PPN portion.")
                % output_tax.name
            )
        ppn_account = tax_lines[0].account_id

        fresh_balance = self._lock()
        partner = self.partner_id
        move = self.env["account.move"].create(
            {
                "journal_id": self.journal_id.id,
                "move_type": "entry",
                "ref": reason,
                "partner_id": partner.id,
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "account_id": counterpart_account.id,
                            "partner_id": partner.id,
                            "name": reason,
                            "debit": gross_amount,
                            "credit": 0.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "account_id": self.account_id.id,
                            "partner_id": partner.id,
                            "name": reason,
                            "debit": 0.0,
                            "credit": dpp,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "account_id": ppn_account.id,
                            "partner_id": partner.id,
                            "name": _("%s (PPN Keluaran)") % reason,
                            "debit": 0.0,
                            "credit": ppn,
                        },
                    ),
                ],
            }
        )
        move.action_post()

        new_balance = fresh_balance + dpp
        WalletMove = self.env["custom.ppob.wallet.move"]
        move_vals = self._build_move_vals(
            {
                "wallet_id": self.id,
                "type": extras.get("move_type") or "topup",
                "amount_signed": dpp,
                "balance_after": new_balance,
                "ref": reason,
                "move_id": move.id,
                "state": "posted",
            },
            extras,
        )
        if "tax_amount" in WalletMove._fields:
            move_vals["tax_amount"] = ppn
        if "gross_amount" in WalletMove._fields:
            move_vals["gross_amount"] = gross_amount
        wallet_move = WalletMove.create(move_vals)
        self.env.cr.execute(
            "UPDATE custom_ppob_wallet SET balance = %s WHERE id = %s",
            (new_balance, self.id),
        )
        self.invalidate_recordset(["balance"])
        return wallet_move

    def action_open_moves(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Wallet Moves"),
            "res_model": "custom.ppob.wallet.move",
            "view_mode": "list,form",
            "domain": [("wallet_id", "=", self.id)],
            "context": {"default_wallet_id": self.id},
        }
