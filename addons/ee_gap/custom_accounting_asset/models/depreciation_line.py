# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class CustomFixedAssetDepreciationLine(models.Model):
    _name = "custom.fixed.asset.depreciation.line"
    _description = "Custom Fixed Asset Depreciation Line"
    _order = "asset_id, sequence, date"

    asset_id = fields.Many2one(
        comodel_name="custom.fixed.asset",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(
        related="asset_id.company_id",
        store=True,
    )
    currency_id = fields.Many2one(
        related="asset_id.currency_id",
        store=True,
    )
    sequence = fields.Integer(required=True)
    date = fields.Date(required=True)
    amount = fields.Monetary(
        required=True,
        currency_field="currency_id",
    )
    posted = fields.Boolean(
        default=False,
        copy=False,
        help="Set once the journal entry has been booked to the GL.",
    )
    reversed = fields.Boolean(
        default=False,
        copy=False,
        help="Set when the posted depreciation has been reversed. A reversed "
        "line is excluded from the accumulated depreciation / NBV and is not "
        "re-posted by the schedule.",
    )
    move_id = fields.Many2one(
        comodel_name="account.move",
        string="Journal Entry",
        readonly=True,
        copy=False,
    )

    def action_post_now(self):
        """Manual posting override — usable when an accountant needs to
        post a single line ahead of the cron schedule.
        """
        for line in self:
            if line.posted:
                continue
            line.asset_id._post_due_depreciation(as_of=line.date)

    def action_reverse(self):
        """Reverse a posted depreciation line.

        Books a reversing journal entry (Dr Accum. / Cr Expense), marks the line
        reversed and un-posted so the accumulated depreciation drops out and the
        asset NBV recomputes (feedback Accounting #4/#19). The line is NOT
        re-posted by the schedule afterwards.

        The monthly entry is normally SHARED by every asset depreciated that
        period (see ``custom.fixed.asset._post_due_depreciation``), so reversing
        the whole move would wipe out the period for every other asset too. When
        the move is shared we therefore book a reversal for this line's amount
        only; a full ``_reverse_moves`` is used just when the line owns its move
        outright.
        """
        for line in self:
            if not line.posted:
                raise UserError(_("Depreciation line #%(seq)s is not posted; nothing to reverse.", seq=line.sequence))
            move = line.move_id
            if move and move.state == "posted":
                siblings = self.search_count(
                    [("move_id", "=", move.id), ("id", "!=", line.id), ("reversed", "=", False)]
                )
                if siblings:
                    line._reverse_partial(move)
                else:
                    # Sole owner of the entry: full reversal, reconciled against it.
                    move._reverse_moves(
                        [
                            {
                                "date": fields.Date.context_today(line),
                                "ref": _(
                                    "Reversal of Depreciation %(code)s #%(seq)s",
                                    code=line.asset_id.code,
                                    seq=line.sequence,
                                ),
                            }
                        ],
                        cancel=True,
                    )
            line.write({"posted": False, "reversed": True})

    def _reverse_partial(self, move):
        """Book a standalone reversal of THIS line only, out of a shared move.

        Dr Accumulated depreciation / Cr Depreciation expense for the line
        amount. Deliberately NOT reconciled against ``move``: the original entry
        stays posted and correct for the other assets it covers, so only the net
        effect of this line is undone.
        """
        self.ensure_one()
        asset = self.asset_id
        reversal = self.env["account.move"].create(
            {
                "date": fields.Date.context_today(self),
                "journal_id": move.journal_id.id,
                "company_id": move.company_id.id,
                "ref": _(
                    "Reversal of Depreciation %(code)s #%(seq)s (from %(origin)s)",
                    code=asset.code,
                    seq=self.sequence,
                    origin=move.name,
                ),
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": _("Reversal Accum. depreciation %(name)s", name=asset.name),
                            "account_id": asset.depreciation_account_id.id,
                            "debit": self.amount,
                            "credit": 0.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "name": _("Reversal Depreciation %(name)s", name=asset.name),
                            "account_id": asset.expense_account_id.id,
                            "debit": 0.0,
                            "credit": self.amount,
                        },
                    ),
                ],
            }
        )
        reversal.action_post()
        return reversal
