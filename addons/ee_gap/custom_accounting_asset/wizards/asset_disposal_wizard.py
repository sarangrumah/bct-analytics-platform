# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CustomFixedAssetDisposalWizard(models.TransientModel):
    _name = "custom.fixed.asset.disposal.wizard"
    _description = "Custom Fixed Asset Disposal Wizard"

    def _default_asset(self):
        return self.env["custom.fixed.asset"].browse(self.env.context.get("default_asset_id"))

    def _default_surplus_account(self):
        asset = self._default_asset()
        if not asset:
            return False
        # Prefer the surplus account actually used by the latest revaluation.
        reval = asset.revaluation_ids.filtered("surplus_account_id")[:1]
        if reval:
            return reval.surplus_account_id.id
        return asset.group_id.default_revaluation_surplus_account_id.id if asset.group_id else False

    def _default_retained_earnings(self):
        asset = self._default_asset()
        grp = asset.group_id if asset else False
        return grp.default_retained_earnings_account_id.id if grp else False

    asset_id = fields.Many2one(
        comodel_name="custom.fixed.asset",
        required=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="asset_id.currency_id",
        readonly=True,
    )
    net_book_value = fields.Monetary(
        related="asset_id.net_book_value",
        readonly=True,
        currency_field="currency_id",
    )
    disposal_date = fields.Date(
        required=True,
        default=fields.Date.context_today,
    )
    disposal_value = fields.Monetary(
        string="Sale / Disposal Proceeds",
        required=True,
        default=0.0,
        currency_field="currency_id",
        help="Amount received from the sale. Zero if the asset is written off.",
    )
    gain_loss = fields.Monetary(
        string="Gain / (Loss)",
        compute="_compute_gain_loss",
        currency_field="currency_id",
    )
    gain_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Gain Account",
        help="Account to credit when proceeds exceed NBV.",
    )
    loss_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Loss Account",
        help="Account to debit when proceeds fall short of NBV.",
    )
    receivable_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Proceeds Account",
        help="Receivable / bank account that will be debited for the proceeds.",
    )
    surplus_balance = fields.Monetary(
        related="asset_id.revaluation_surplus_balance",
        readonly=True,
        currency_field="currency_id",
    )
    surplus_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Revaluation Surplus Account",
        default=_default_surplus_account,
        help="Equity account holding the revaluation surplus to be released.",
    )
    retained_earnings_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Retained Earnings Account",
        default=_default_retained_earnings,
        help="Equity account the remaining revaluation surplus is transferred to on "
        "disposal (IAS 16.41). Not routed through profit or loss.",
    )
    create_journal_entry = fields.Boolean(
        default=True,
        help="If checked, a journal entry will be generated that retires the "
        "asset cost, reverses accumulated depreciation, books proceeds, "
        "and records gain/loss.",
    )
    note = fields.Text()

    @api.depends("disposal_value", "asset_id.net_book_value")
    def _compute_gain_loss(self):
        for wiz in self:
            wiz.gain_loss = (wiz.disposal_value or 0.0) - (wiz.asset_id.net_book_value or 0.0)

    def action_dispose(self):
        self.ensure_one()
        asset = self.asset_id
        if asset.state != "running":
            raise UserError(_("Only running assets can be disposed."))

        move = False
        if self.create_journal_entry:
            move = self._create_disposal_move()

        disposal_vals = {
            "state": "disposed",
            "disposal_date": self.disposal_date,
            "disposal_value": self.disposal_value,
            "disposal_gain_loss": self.gain_loss,
            "disposal_move_id": move.id if move else False,
        }
        # The surplus is transferred to retained earnings inside the disposal move;
        # clear the running balance so it is not double-counted on a later action.
        if move and asset.revaluation_surplus_balance:
            disposal_vals["revaluation_surplus_balance"] = 0.0
        asset.write(disposal_vals)
        asset.message_post(
            body=_(
                "Asset disposed on %(date)s. Proceeds: %(value)s, gain/(loss): %(gain)s. %(note)s",
                date=self.disposal_date,
                value=self.disposal_value,
                gain=self.gain_loss,
                note=self.note or "",
            )
        )
        return {"type": "ir.actions.act_window_close"}

    def _create_disposal_move(self):
        """Create a balanced disposal journal entry:
        DR Accumulated depreciation       (release accumulated)
        DR Proceeds account               (sale amount)
        DR Loss account (if loss)
              CR Asset account            (release asset cost)
              CR Gain account (if gain)
        """
        self.ensure_one()
        asset = self.asset_id
        if not asset.asset_account_id or not asset.depreciation_account_id:
            raise UserError(
                _("Asset and accumulated depreciation accounts must be set on the asset to post a disposal entry.")
            )
        if self.disposal_value and not self.receivable_account_id:
            raise UserError(_("Proceeds account is required when disposal value > 0."))
        if self.gain_loss > 0 and not self.gain_account_id:
            raise UserError(_("Gain account is required when proceeds exceed NBV."))
        if self.gain_loss < 0 and not self.loss_account_id:
            raise UserError(_("Loss account is required when proceeds fall short of NBV."))
        surplus = asset.revaluation_surplus_balance or 0.0
        if surplus > 0 and (not self.surplus_account_id or not self.retained_earnings_account_id):
            raise UserError(
                _(
                    "This asset carries a revaluation surplus of %(amount)s. A "
                    "revaluation surplus account and a retained earnings account are "
                    "required to transfer it on disposal.",
                    amount=surplus,
                )
            )

        accum = asset.accumulated_depreciation
        # Release the full carrying amount held in the asset account, including
        # any cumulative revaluation booked over the asset's life.
        cost = asset.acquisition_value + (asset.revaluation_value or 0.0)
        proceeds = self.disposal_value
        gain = self.gain_loss

        lines = []
        if accum:
            lines.append(
                (
                    0,
                    0,
                    {
                        "name": _("Release accum. depreciation"),
                        "account_id": asset.depreciation_account_id.id,
                        "debit": accum,
                        "credit": 0.0,
                    },
                )
            )
        if proceeds:
            lines.append(
                (
                    0,
                    0,
                    {
                        "name": _("Disposal proceeds"),
                        "account_id": self.receivable_account_id.id,
                        "debit": proceeds,
                        "credit": 0.0,
                    },
                )
            )
        if gain < 0:
            lines.append(
                (
                    0,
                    0,
                    {
                        "name": _("Loss on disposal"),
                        "account_id": self.loss_account_id.id,
                        "debit": abs(gain),
                        "credit": 0.0,
                    },
                )
            )
        lines.append(
            (
                0,
                0,
                {
                    "name": _("Release asset cost"),
                    "account_id": asset.asset_account_id.id,
                    "debit": 0.0,
                    "credit": cost,
                },
            )
        )
        if gain > 0:
            lines.append(
                (
                    0,
                    0,
                    {
                        "name": _("Gain on disposal"),
                        "account_id": self.gain_account_id.id,
                        "debit": 0.0,
                        "credit": gain,
                    },
                )
            )
        # IAS 16.41 — transfer any remaining revaluation surplus to retained
        # earnings (an equity-to-equity movement; not through P&L). Balanced within
        # itself, so it keeps the disposal move balanced.
        if surplus > 0:
            lines.append(
                (
                    0,
                    0,
                    {
                        "name": _("Release revaluation surplus"),
                        "account_id": self.surplus_account_id.id,
                        "debit": surplus,
                        "credit": 0.0,
                    },
                )
            )
            lines.append(
                (
                    0,
                    0,
                    {
                        "name": _("Revaluation surplus to retained earnings"),
                        "account_id": self.retained_earnings_account_id.id,
                        "debit": 0.0,
                        "credit": surplus,
                    },
                )
            )

        move = self.env["account.move"].create(
            {
                "date": self.disposal_date,
                "journal_id": asset.journal_id.id,
                "company_id": asset.company_id.id,
                "ref": _("Disposal %(code)s", code=asset.code),
                "line_ids": lines,
            }
        )
        move.action_post()
        return move
