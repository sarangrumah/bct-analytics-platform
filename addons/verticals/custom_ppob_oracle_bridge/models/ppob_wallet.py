# -*- coding: utf-8 -*-
"""Extend custom.ppob.wallet with a mirror_source flag + native-mutation guard.

When mirror_source='oracle', the balance is authoritative in MSG019T; native
debit/credit is blocked to prevent double-debit -- only the balance-mirror cron
writes (via move_type='oracle_sync', which is whitelisted).
"""

from odoo import _, fields, models
from odoo.exceptions import UserError


class PpobWallet(models.Model):
    _inherit = "custom.ppob.wallet"

    mirror_source = fields.Selection(
        selection=[
            ("native", "Odoo Native"),
            ("oracle", "Oracle MSG019T Mirror"),
        ],
        default="native",
        required=True,
        help="Authority for this wallet balance. native: Odoo is the source of "
        "truth. oracle: balance mirrored from MSG019T; native debit/credit "
        "is blocked (only the mirror cron writes via oracle_sync).",
    )

    def _check_oracle_mirror_guard(self, move_type):
        for rec in self:
            if rec.mirror_source == "oracle" and move_type != "oracle_sync":
                raise UserError(
                    _(
                        "Wallet '%s' bersumber Oracle (mirror). Mutasi native tidak "
                        "diizinkan; saldo dikelola oleh SP Oracle dan di-sync via "
                        "cron. (attempted move_type=%s)"
                    )
                    % (rec.display_name, move_type or "unknown")
                )

    def _atomic_debit(self, amount, reason, counterpart_account, **extras):
        self._check_oracle_mirror_guard(extras.get("move_type"))
        return super()._atomic_debit(amount, reason, counterpart_account, **extras)

    def _atomic_credit(self, amount, reason, counterpart_account, **extras):
        self._check_oracle_mirror_guard(extras.get("move_type"))
        return super()._atomic_credit(amount, reason, counterpart_account, **extras)
