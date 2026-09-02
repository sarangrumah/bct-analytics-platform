# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CustomFixedAssetRevaluationWizard(models.TransientModel):
    _name = "custom.fixed.asset.revaluation.wizard"
    _description = "Custom Fixed Asset Revaluation Wizard"

    def _default_asset(self):
        return self.env["custom.fixed.asset"].browse(self.env.context.get("default_asset_id"))

    def _default_new_value(self):
        asset = self._default_asset()
        return asset.net_book_value if asset else 0.0

    def _default_remaining_life(self):
        asset = self._default_asset()
        if not asset:
            return 0
        posted = len(asset.depreciation_line_ids.filtered("posted"))
        return max(0, asset.useful_life_months - posted)

    def _default_journal(self):
        asset = self._default_asset()
        return asset.journal_id.id if asset and asset.journal_id else False

    def _default_surplus_account(self):
        grp = self._default_asset().group_id
        return grp.default_revaluation_surplus_account_id.id if grp else False

    def _default_loss_account(self):
        grp = self._default_asset().group_id
        return grp.default_revaluation_loss_account_id.id if grp else False

    def _default_income_account(self):
        grp = self._default_asset().group_id
        return grp.default_revaluation_income_account_id.id if grp else False

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
        string="Current Net Book Value",
        related="asset_id.net_book_value",
        readonly=True,
        currency_field="currency_id",
    )
    revaluation_date = fields.Date(
        required=True,
        default=fields.Date.context_today,
    )
    new_value = fields.Monetary(
        string="New Value",
        required=True,
        default=_default_new_value,
        currency_field="currency_id",
        help="New carrying (net book) value of the asset after revaluation.",
    )
    revaluation_amount = fields.Monetary(
        string="Adjustment",
        compute="_compute_revaluation_amount",
        currency_field="currency_id",
        help="New value minus current net book value. Positive = upward revaluation.",
    )
    new_remaining_life = fields.Integer(
        string="Remaining Useful Life (months)",
        default=_default_remaining_life,
        help="Number of months over which the new value is depreciated going "
        "forward. Leave as suggested to keep the remaining life unchanged.",
    )
    surplus_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Revaluation Surplus Account",
        default=_default_surplus_account,
        help="Equity / OCI account credited for an upward revaluation (or debited "
        "when a downward revaluation offsets an existing surplus).",
    )
    loss_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Revaluation Loss Account",
        default=_default_loss_account,
        help="P&L expense account debited for the part of a downward revaluation not covered by an existing surplus.",
    )
    income_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Revaluation Income Account",
        default=_default_income_account,
        help="P&L income account credited when an upward revaluation reverses a "
        "prior downward revaluation that was expensed.",
    )
    journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Journal",
        default=_default_journal,
        domain="[('type', '=', 'general'), ('company_id', '=', company_id)]",
    )
    company_id = fields.Many2one(
        related="asset_id.company_id",
        readonly=True,
    )
    note = fields.Text()

    @api.depends("new_value", "asset_id.net_book_value")
    def _compute_revaluation_amount(self):
        for wiz in self:
            wiz.revaluation_amount = (wiz.new_value or 0.0) - (wiz.asset_id.net_book_value or 0.0)

    def action_revalue(self):
        self.ensure_one()
        asset = self.asset_id
        if asset.state != "running":
            raise UserError(_("Only running assets can be revalued."))
        if not asset.asset_account_id:
            raise UserError(_("The asset account must be set on the asset before revaluing."))
        if self.new_remaining_life and self.new_remaining_life < 0:
            raise UserError(_("Remaining useful life cannot be negative."))

        increment = (self.new_value or 0.0) - (asset.net_book_value or 0.0)
        if asset.currency_id.is_zero(increment):
            raise UserError(_("The new value equals the current net book value; nothing to revalue."))

        # IAS 16 split between equity (revaluation surplus) and P&L.
        surplus_balance = asset.revaluation_surplus_balance or 0.0
        loss_recognized = asset.revaluation_loss_recognized or 0.0
        if increment > 0:
            # Upward: reverse any prior P&L loss first (income), remainder to surplus.
            income_amt = min(increment, loss_recognized)
            surplus_amt = increment - income_amt
            to_pl = 0.0
            from_surplus = 0.0
            new_surplus_balance = surplus_balance + surplus_amt
            new_loss_recognized = loss_recognized - income_amt
        else:
            # Downward: offset existing surplus first, remainder to P&L loss.
            decrease = -increment
            from_surplus = min(decrease, surplus_balance)
            to_pl = decrease - from_surplus
            income_amt = 0.0
            surplus_amt = 0.0
            new_surplus_balance = surplus_balance - from_surplus
            new_loss_recognized = loss_recognized + to_pl

        if surplus_amt > 0 and not self.surplus_account_id:
            raise UserError(_("A revaluation surplus account is required to credit the surplus."))
        if from_surplus > 0 and not self.surplus_account_id:
            raise UserError(_("A revaluation surplus account is required to offset the existing surplus."))
        if income_amt > 0 and not self.income_account_id:
            raise UserError(_("A revaluation income account is required to reverse the prior downward revaluation."))
        if to_pl > 0 and not self.loss_account_id:
            raise UserError(_("A revaluation loss account is required for the P&L part of the decrease."))

        nbv_before = asset.net_book_value
        useful_life_before = asset.useful_life_months
        posted_count = len(asset.depreciation_line_ids.filtered("posted"))

        move = self._create_revaluation_move(increment, income_amt, surplus_amt, from_surplus, to_pl)

        vals = {
            "revaluation_value": asset.revaluation_value + increment,
            "revaluation_surplus_balance": new_surplus_balance,
            "revaluation_loss_recognized": new_loss_recognized,
        }
        if self.new_remaining_life:
            vals["useful_life_months"] = posted_count + self.new_remaining_life
        asset.write(vals)
        # Rebuild the unposted tail: posted lines are preserved, future lines are
        # re-spread over the (possibly revised) remaining life on the new value.
        asset._build_schedule()

        remaining_after = asset.useful_life_months - posted_count
        self.env["custom.fixed.asset.revaluation"].create(
            {
                "asset_id": asset.id,
                "name": _("Revaluation %(code)s", code=asset.code),
                "revaluation_date": self.revaluation_date,
                "net_book_value_before": nbv_before,
                "new_value": self.new_value,
                "revaluation_amount": increment,
                "surplus_movement": surplus_amt - from_surplus,
                "pl_movement": income_amt - to_pl,
                "useful_life_before": useful_life_before,
                "remaining_life_after": remaining_after,
                "surplus_balance_after": new_surplus_balance,
                "loss_recognized_after": new_loss_recognized,
                "surplus_account_id": self.surplus_account_id.id or False,
                "loss_account_id": self.loss_account_id.id or False,
                "income_account_id": self.income_account_id.id or False,
                "journal_id": (self.journal_id or asset.journal_id).id or False,
                "move_id": move.id,
                "note": self.note or False,
            }
        )
        asset.message_post(
            body=_(
                "Asset revalued on %(date)s. NBV %(before)s → %(after)s "
                "(adjustment %(amount)s). Remaining life: %(life)s month(s). %(note)s",
                date=self.revaluation_date,
                before=nbv_before,
                after=self.new_value,
                amount=increment,
                life=remaining_after,
                note=self.note or "",
            )
        )
        return {"type": "ir.actions.act_window_close"}

    def _create_revaluation_move(self, increment, income_amt, surplus_amt, from_surplus, to_pl):
        """Book the revaluation adjustment against the asset account, splitting the
        equity (revaluation surplus) and P&L parts per IAS 16:

        upward   → DR asset / CR revaluation income (loss reversal) + CR surplus
        downward → DR revaluation surplus (offset) + DR revaluation loss / CR asset

        Previously posted depreciation entries are never touched.
        """
        self.ensure_one()
        asset = self.asset_id
        journal = self.journal_id or asset.journal_id
        if not journal:
            raise UserError(_("A journal is required to post the revaluation entry."))

        lines = []

        def _line(account, debit, credit, name):
            lines.append((0, 0, {"name": name, "account_id": account.id, "debit": debit, "credit": credit}))

        if increment > 0:
            _line(asset.asset_account_id, increment, 0.0, _("Revaluation increase %(name)s", name=asset.name))
            if income_amt > 0:
                _line(self.income_account_id, 0.0, income_amt, _("Revaluation income %(name)s", name=asset.name))
            if surplus_amt > 0:
                _line(self.surplus_account_id, 0.0, surplus_amt, _("Revaluation surplus %(name)s", name=asset.name))
        else:
            decrease = -increment
            _line(asset.asset_account_id, 0.0, decrease, _("Revaluation decrease %(name)s", name=asset.name))
            if from_surplus > 0:
                _line(self.surplus_account_id, from_surplus, 0.0, _("Surplus offset %(name)s", name=asset.name))
            if to_pl > 0:
                _line(self.loss_account_id, to_pl, 0.0, _("Revaluation loss %(name)s", name=asset.name))

        move = self.env["account.move"].create(
            {
                "date": self.revaluation_date,
                "journal_id": journal.id,
                "company_id": asset.company_id.id,
                "ref": _("Revaluation %(code)s", code=asset.code),
                "line_ids": lines,
            }
        )
        move.action_post()
        return move
