# -*- coding: utf-8 -*-
"""account.move extension marking provider DP 100% / Pelunasan vendor bills.

The wizard ``custom.ppob.provider.topup.wizard`` builds a pair of vendor bills
(DP + Pelunasan) for each topup; these fields tie them back to the bucket
subledger and let the ``_post()`` hook advance the bucket balance according to
the provider's ``topup_dp_timing`` config.

Note vs. ERA source: the era_accounting "report tag" classification guard
(``_era_assert_classification_complete``) is intentionally NOT ported -- the
platform has no such classification system, so there is nothing to exempt.
"""

import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    x_custom_ppob_dp_role = fields.Selection(
        selection=[
            ("dp_100", "Provider DP 100%"),
            ("pelunasan", "Provider Pelunasan"),
        ],
        copy=False,
        index=True,
        help="Marks this move as part of a provider deposit DP/Pelunasan pair "
        "created by custom.ppob.provider.topup.wizard.",
    )
    x_custom_ppob_dp_bill_id = fields.Many2one(
        comodel_name="account.move",
        string="Provider DP Bill",
        copy=False,
        help="On a Pelunasan bill, links back to its DP 100% counterpart.",
    )
    x_custom_ppob_pelunasan_bill_id = fields.Many2one(
        comodel_name="account.move",
        string="Provider Pelunasan Bill",
        copy=False,
        help="On a DP 100% bill, links forward to its Pelunasan counterpart.",
    )
    x_custom_ppob_bucket_id = fields.Many2one(
        comodel_name="custom.ppob.provider.bucket",
        string="Provider Bucket",
        copy=False,
        help="Subledger row credited when this DP / Pelunasan bill posts, depending on provider.topup_dp_timing.",
    )
    x_custom_ppob_topup_log_id = fields.Many2one(
        comodel_name="custom.ppob.provider.topup.log",
        string="Provider Topup Request",
        copy=False,
    )

    # ------------------------------------------------------------------
    # Posting hook
    # ------------------------------------------------------------------

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        for move in posted.filtered("x_custom_ppob_dp_role"):
            move._ppob_provider_handle_bucket_credit()
        return posted

    def _ppob_provider_handle_bucket_credit(self):
        """Advance bucket subledger when posting timing matches provider config."""
        self.ensure_one()
        bucket = self.x_custom_ppob_bucket_id
        if not bucket:
            return
        # Skip flag for callers that already drove the bucket credit themselves.
        if self.env.context.get("ppob_skip_bucket_post"):
            return
        provider = bucket.provider_id
        timing = provider.topup_dp_timing
        role = self.x_custom_ppob_dp_role
        should_credit = (timing == "dp_post" and role == "dp_100") or (
            timing == "pelunasan_post" and role == "pelunasan"
        )
        if not should_credit:
            return

        # Authoritative source for the bucket credit is the GL itself: sum the
        # debit-credit balance on the bucket account across this move's lines.
        # This guarantees subledger == GL even when Odoo's tax engine rounds the
        # DPP/PPN split differently from the wizard's pre-computed amounts.
        dpp = sum(line.balance for line in self.line_ids if line.account_id == bucket.account_id)
        log = self.x_custom_ppob_topup_log_id
        ppn = log.ppn_amount if log else 0.0
        gross = log.gross_amount if log else abs(self.amount_total)

        if dpp <= 0:
            _logger.warning(
                "ppob provider DP hook: move %s has non-positive bucket delta %.2f, skipping bucket credit.",
                self.name,
                dpp,
            )
            return

        bucket._atomic_credit_from_move(
            self,
            dpp_amount=dpp,
            tax_amount=ppn,
            gross_amount=gross,
            ref=self.ref or self.name,
            move_type="topup",
        )
        if log and log.state == "draft":
            log.state = "posted"
