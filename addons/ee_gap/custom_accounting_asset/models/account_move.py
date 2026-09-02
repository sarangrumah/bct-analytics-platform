# -*- coding: utf-8 -*-
"""Keep fixed-asset depreciation lines in sync with their journal entries.

A depreciation line records ``posted`` + ``move_id`` when the schedule books its
entry. If an accountant later resets that entry to draft, cancels or deletes it
directly in Accounting (rather than via the asset's Reverse action), the line
must not stay ``posted`` — otherwise the accumulated depreciation / NBV are
overstated and the period can never be re-posted (feedback Accounting #4/#20).

These overrides self-heal the linked lines:
* entry set to draft / cancelled -> line un-posted (NBV recomputes);
* entry deleted                 -> line un-posted and detached, so the schedule
                                    can post it again.
"""

from odoo import models


class AccountMove(models.Model):
    _inherit = "account.move"

    def _retail_asset_depreciation_lines(self):
        """Depreciation lines whose journal entry is in ``self``."""
        if not self:
            return self.env["custom.fixed.asset.depreciation.line"]
        return self.env["custom.fixed.asset.depreciation.line"].search([("move_id", "in", self.ids)])

    def button_draft(self):
        res = super().button_draft()
        # Entry pulled back to draft (or cancelled) -> the depreciation is no
        # longer in the GL, so drop it from the accumulated depreciation.
        lines = self._retail_asset_depreciation_lines().filtered("posted")
        if lines:
            lines.write({"posted": False})
        return res

    def unlink(self):
        # Detach before the FK is nulled so the lines become re-postable and the
        # NBV recomputes. reversed stays False -> the schedule will post again.
        lines = self._retail_asset_depreciation_lines().filtered("posted")
        if lines:
            lines.write({"posted": False, "move_id": False})
        return super().unlink()
