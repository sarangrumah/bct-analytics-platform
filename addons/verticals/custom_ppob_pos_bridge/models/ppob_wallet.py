# -*- coding: utf-8 -*-
"""Extend custom.ppob.wallet for POS mirror mode.

When ``pos_mirror`` is set, the mitra balance is authoritative in POS
POS; Odoo only mirrors it. Two consequences:

1. Native engine mutations (``_atomic_debit`` / ``_atomic_credit``, used by
   ``custom_ppob_sale._dispatch_one``) are BLOCKED -- the native engine must
   never run against a mirror wallet.
2. The mirror feed writes via ``_mirror_debit`` / ``_mirror_credit``, which post
   the same paired GL entry and sub-ledger row but WITHOUT the credit-limit
   ceiling: the two feeds arrive independently and possibly out of order
   (doc S6.2), so posting must never be blocked by a not-yet-mirrored top-up.
   They still serialise on the same ``SELECT ... FOR UPDATE`` row lock.
"""

from odoo import _, fields, models
from odoo.exceptions import UserError

from .constants import MIRROR_MOVE_TYPES


class PpobWallet(models.Model):
    _inherit = "custom.ppob.wallet"

    pos_mirror = fields.Boolean(
        string="POS Mirror",
        default=False,
        help="When set, this wallet mirrors an POS POS balance. Native "
        "debit/credit is blocked; only the POS feed writes it via "
        "_mirror_debit/_mirror_credit (no credit-limit ceiling).",
    )

    # ------------------------------------------------------------------
    # Native-mutation guard
    # ------------------------------------------------------------------

    def _check_pos_mirror_guard(self, move_type):
        for rec in self:
            if rec.pos_mirror and move_type not in MIRROR_MOVE_TYPES:
                raise UserError(
                    _(
                        "Wallet '%(w)s' is an POS mirror; native mutation is "
                        "not allowed. Balance is authoritative at POS POS and "
                        "is projected via the mirror feed. (move_type=%(t)s)"
                    )
                    % {"w": rec.display_name, "t": move_type or "unknown"}
                )

    def _atomic_debit(self, amount, reason, counterpart_account, **extras):
        self._check_pos_mirror_guard(extras.get("move_type"))
        return super()._atomic_debit(amount, reason, counterpart_account, **extras)

    def _atomic_credit(self, amount, reason, counterpart_account, **extras):
        self._check_pos_mirror_guard(extras.get("move_type"))
        return super()._atomic_credit(amount, reason, counterpart_account, **extras)

    # ------------------------------------------------------------------
    # Mirror-mode posting (no ceiling)
    # ------------------------------------------------------------------

    def _mirror_debit(self, amount, reason, counterpart_account, move_type, **extras):
        """Mirror a wallet drawdown (POS sale): Dr wallet-liability / Cr
        counterpart(revenue). Lowers the mirror balance without the native
        credit ceiling. Reuses the base posting + sub-ledger helpers."""
        self.ensure_one()
        if amount <= 0:
            raise UserError(_("Mirror debit amount must be positive."))
        fresh_balance = self._lock()
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
                "type": move_type,
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

    def _mirror_credit(self, amount, reason, counterpart_account, move_type, **extras):
        """Mirror a wallet increase (top-up: Dr bank / Cr wallet-liability;
        refund: Dr revenue / Cr wallet-liability). Raises the mirror balance."""
        self.ensure_one()
        if amount <= 0:
            raise UserError(_("Mirror credit amount must be positive."))
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
                "type": move_type,
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

    def _sync_mirror_balance(self, target_balance, reason):
        """Snap the mirror balance to an authoritative POS value, recording
        the delta as an pos_sync sub-ledger row (no GL). Used by
        reconciliation snapshot to correct drift."""
        self.ensure_one()
        fresh = self._lock()
        delta = (target_balance or 0.0) - fresh
        if abs(delta) <= 0.0001:
            return False
        self.env["custom.ppob.wallet.move"].create(
            {
                "wallet_id": self.id,
                "type": "pos_sync",
                "amount_signed": delta,
                "balance_after": target_balance,
                "ref": reason,
                "state": "posted",
            }
        )
        self.env.cr.execute(
            "UPDATE custom_ppob_wallet SET balance = %s WHERE id = %s",
            (target_balance, self.id),
        )
        self.invalidate_recordset(["balance"])
        return True
