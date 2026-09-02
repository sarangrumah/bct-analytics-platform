# -*- coding: utf-8 -*-
"""Backfill wizard: bulk-replay skipped POS events (recovery / onboarding).

Unlike the Oracle bridge (which pulls a MSG016T id range), the POS bridge
is push-only, so there is no upstream to re-poll. "Backfill" therefore means
re-processing the skipped queue after mappings are completed -- e.g. once a new
mitra/SKU is mapped, replay every event that skipped for that reason.
"""

from odoo import _, fields, models


class PosBackfillWizard(models.TransientModel):
    _name = "custom.ppob.pos.backfill.wizard"
    _description = "POS Bridge: Backfill / Replay Skipped Events"

    date_from = fields.Datetime()
    date_to = fields.Datetime()
    feed = fields.Selection(
        selection=[("all", "All"), ("pos", "POS"), ("h2h", "H2H")],
        default="all",
        required=True,
    )
    skip_reason = fields.Selection(
        selection=[
            ("all", "All reasons"),
            ("mitra_not_mapped", "Mitra not mapped"),
            ("product_not_mapped", "Product not mapped"),
            ("non_terminal_status", "Non-terminal status"),
            ("bad_payload", "Bad payload"),
            ("post_error", "Post error"),
        ],
        default="all",
        required=True,
    )

    def _domain(self):
        domain = [("replayed", "=", False)]
        if self.feed != "all":
            domain.append(("feed", "=", self.feed))
        if self.skip_reason != "all":
            domain.append(("skip_reason", "=", self.skip_reason))
        if self.date_from:
            domain.append(("create_date", ">=", self.date_from))
        if self.date_to:
            domain.append(("create_date", "<=", self.date_to))
        return domain

    def action_replay(self):
        self.ensure_one()
        skipped = self.env["custom.ppob.pos.ingest.skipped"].search(self._domain())
        skipped.action_replay()
        replayed = len(skipped.filtered("replayed"))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Backfill"),
                "message": _("%(done)s of %(total)s skipped event(s) replayed.")
                % {"done": replayed, "total": len(skipped)},
                "type": "success" if replayed else "warning",
                "sticky": False,
            },
        }
