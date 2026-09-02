# -*- coding: utf-8 -*-
import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class CustomFixedAsset(models.Model):
    _name = "custom.fixed.asset"
    _description = "Custom Fixed Asset"
    _inherit = ["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]
    _order = "code, id"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(
        required=True,
        copy=False,
        default=lambda self: self.env._("New"),
        tracking=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id",
        readonly=True,
    )
    group_id = fields.Many2one(
        comodel_name="custom.fixed.asset.group",
        string="Group",
        tracking=True,
    )
    location_id = fields.Many2one(
        comodel_name="custom.fixed.asset.location",
        string="Location",
        tracking=True,
    )
    custodian_id = fields.Many2one(
        comodel_name="res.users",
        string="Custodian",
        tracking=True,
    )
    note = fields.Html()

    # ------------------------------------------------------------------
    # Acquisition
    # ------------------------------------------------------------------
    acquisition_date = fields.Date(
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    posting_date = fields.Date(
        string="Posting Date",
        tracking=True,
        copy=False,
        help="Reference date used to schedule and date each depreciation entry. "
        "Falls back to the acquisition date when left empty.",
    )
    depreciation_date_mode = fields.Selection(
        selection=[
            ("specific", "Specific date (same day as posting date)"),
            ("next_month", "Specific date, next month"),
            ("end_following_month", "End of the following month"),
        ],
        string="Depreciation Date Rule",
        default="next_month",
        required=True,
        help="How each depreciation line date is derived from the posting date.",
    )
    acquisition_value = fields.Monetary(
        required=True,
        currency_field="currency_id",
        tracking=True,
    )
    salvage_value = fields.Monetary(
        default=0.0,
        currency_field="currency_id",
    )
    revaluation_value = fields.Monetary(
        string="Cumulative Revaluation",
        default=0.0,
        currency_field="currency_id",
        readonly=True,
        copy=False,
        tracking=True,
        help="Net cumulative revaluation booked to the asset account. Positive for "
        "upward revaluations, negative for downward ones.",
    )
    revaluation_surplus_balance = fields.Monetary(
        string="Revaluation Surplus Balance",
        default=0.0,
        currency_field="currency_id",
        readonly=True,
        copy=False,
        tracking=True,
        help="Credit balance of the revaluation surplus (equity) held for this asset. "
        "A downward revaluation offsets this before hitting P&L; the remainder is "
        "transferred to retained earnings on disposal.",
    )
    revaluation_loss_recognized = fields.Monetary(
        string="Revaluation Loss Recognized",
        default=0.0,
        currency_field="currency_id",
        readonly=True,
        copy=False,
        tracking=True,
        help="Cumulative downward revaluation expensed to P&L that a future upward "
        "revaluation reverses (as income) before crediting surplus.",
    )
    useful_life_months = fields.Integer(
        string="Useful Life (months)",
        required=True,
        default=60,
    )
    depreciation_method = fields.Selection(
        selection=[
            ("straight_line", "Straight line"),
            ("declining", "Declining balance"),
            ("none", "No depreciation"),
        ],
        default="straight_line",
        required=True,
    )
    declining_factor = fields.Float(
        string="Declining Factor",
        default=2.0,
        help="Factor applied to the straight-line rate when method = declining balance (e.g. 2.0 = double declining).",
    )

    # ------------------------------------------------------------------
    # Accounts (override of group defaults)
    # ------------------------------------------------------------------
    asset_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Asset Account",
    )
    depreciation_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Accumulated Depreciation Account",
    )
    expense_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Depreciation Expense Account",
    )
    journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Depreciation Journal",
        domain="[('type', '=', 'general'), ('company_id', '=', company_id)]",
    )

    # ------------------------------------------------------------------
    # Schedule + computed totals
    # ------------------------------------------------------------------
    depreciation_line_ids = fields.One2many(
        comodel_name="custom.fixed.asset.depreciation.line",
        inverse_name="asset_id",
        string="Depreciation Schedule",
        copy=False,
    )
    accumulated_depreciation = fields.Monetary(
        compute="_compute_depreciation_totals",
        currency_field="currency_id",
        store=False,
    )
    net_book_value = fields.Monetary(
        compute="_compute_depreciation_totals",
        currency_field="currency_id",
        store=False,
    )

    # ------------------------------------------------------------------
    # Revaluation history
    # ------------------------------------------------------------------
    revaluation_ids = fields.One2many(
        comodel_name="custom.fixed.asset.revaluation",
        inverse_name="asset_id",
        string="Revaluations",
        copy=False,
    )
    revaluation_count = fields.Integer(
        compute="_compute_revaluation_count",
    )

    # ------------------------------------------------------------------
    # State / disposal
    # ------------------------------------------------------------------
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("running", "Running"),
            ("disposed", "Disposed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        tracking=True,
        copy=False,
    )
    disposal_date = fields.Date(readonly=True, copy=False)
    disposal_value = fields.Monetary(
        currency_field="currency_id",
        readonly=True,
        copy=False,
    )
    disposal_gain_loss = fields.Monetary(
        currency_field="currency_id",
        readonly=True,
        copy=False,
    )
    disposal_move_id = fields.Many2one(
        comodel_name="account.move",
        string="Disposal Journal Entry",
        readonly=True,
        copy=False,
    )

    _code_company_unique = models.Constraint(
        "UNIQUE(code, company_id)",
        "Asset code must be unique within a company.",
    )

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains("useful_life_months")
    def _check_useful_life(self):
        for asset in self:
            if asset.depreciation_method != "none" and asset.useful_life_months < 1:
                raise ValidationError(
                    _(
                        'Asset "%(name)s": useful life must be at least 1 month.',
                        name=asset.name,
                    )
                )

    @api.constrains("salvage_value", "acquisition_value")
    def _check_salvage(self):
        for asset in self:
            if asset.salvage_value < 0:
                raise ValidationError(
                    _(
                        'Asset "%(name)s": salvage value cannot be negative.',
                        name=asset.name,
                    )
                )
            if asset.salvage_value > asset.acquisition_value:
                raise ValidationError(
                    _(
                        'Asset "%(name)s": salvage value cannot exceed acquisition value.',
                        name=asset.name,
                    )
                )

    @api.constrains("declining_factor", "depreciation_method")
    def _check_declining_factor(self):
        for asset in self:
            if asset.depreciation_method == "declining" and asset.declining_factor <= 0:
                raise ValidationError(_("Declining factor must be strictly positive."))

    # ------------------------------------------------------------------
    # On-change: pull defaults from group
    # ------------------------------------------------------------------
    @api.onchange("acquisition_date")
    def _onchange_acquisition_date(self):
        for asset in self:
            if asset.acquisition_date and not asset.posting_date:
                asset.posting_date = asset.acquisition_date

    @api.onchange("group_id")
    def _onchange_group_id(self):
        for asset in self:
            if not asset.group_id:
                continue
            grp = asset.group_id
            if grp.default_useful_life_months and not asset.useful_life_months:
                asset.useful_life_months = grp.default_useful_life_months
            if grp.default_asset_account_id and not asset.asset_account_id:
                asset.asset_account_id = grp.default_asset_account_id
            if grp.default_depreciation_account_id and not asset.depreciation_account_id:
                asset.depreciation_account_id = grp.default_depreciation_account_id
            if grp.default_expense_account_id and not asset.expense_account_id:
                asset.expense_account_id = grp.default_expense_account_id
            if grp.default_journal_id and not asset.journal_id:
                asset.journal_id = grp.default_journal_id

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends(
        "depreciation_line_ids.amount",
        "depreciation_line_ids.posted",
        "depreciation_line_ids.reversed",
        "acquisition_value",
        "revaluation_value",
    )
    def _compute_depreciation_totals(self):
        for asset in self:
            posted = asset.depreciation_line_ids.filtered("posted")
            accum = sum(posted.mapped("amount"))
            asset.accumulated_depreciation = accum
            asset.net_book_value = (asset.acquisition_value or 0.0) + (asset.revaluation_value or 0.0) - accum

    def _compute_revaluation_count(self):
        for asset in self:
            asset.revaluation_count = len(asset.revaluation_ids)

    # ------------------------------------------------------------------
    # Schedule generation
    # ------------------------------------------------------------------
    def _depreciable_base(self):
        self.ensure_one()
        return max(
            0.0,
            self.acquisition_value + (self.revaluation_value or 0.0) - (self.salvage_value or 0.0),
        )

    def _depreciation_date_for(self, seq_number):
        """Return the date of the depreciation line with the given month ordinal,
        honouring ``depreciation_date_mode`` and anchored on ``posting_date``.
        """
        self.ensure_one()
        start = self.posting_date or self.acquisition_date or fields.Date.context_today(self)
        mode = self.depreciation_date_mode or "next_month"
        if mode == "specific":
            # Line 1 lands exactly on the posting date, then monthly increments.
            return start + relativedelta(months=seq_number - 1)
        if mode == "end_following_month":
            # Last day of the month that follows the anchor by ``seq_number`` months.
            return start + relativedelta(months=seq_number, day=31)
        # next_month (default) — one month after the anchor for line 1.
        return start + relativedelta(months=seq_number)

    def _build_schedule(self):
        """Regenerate the depreciation schedule. Already-posted lines are
        preserved; remaining unposted lines are recomputed from the asset's
        current parameters.
        """
        self.ensure_one()
        if self.depreciation_method == "none":
            return
        base = self._depreciable_base()
        months = self.useful_life_months
        if months <= 0 or base <= 0:
            return

        # Drop unposted lines so we can rebuild from current parameters. Reversed
        # lines are kept for audit (posted=False but excluded from scheduling).
        self.depreciation_line_ids.filtered(lambda l: not l.posted and not l.reversed).unlink()
        posted_amount = sum(self.depreciation_line_ids.filtered("posted").mapped("amount"))
        remaining = max(0.0, base - posted_amount)
        if remaining <= 0:
            return

        first_seq = max(self.depreciation_line_ids.mapped("sequence")) + 1 if self.depreciation_line_ids else 1
        months_left = months - len(self.depreciation_line_ids.filtered("posted"))
        if months_left <= 0:
            return

        vals_list = []
        if self.depreciation_method == "straight_line":
            monthly = round(remaining / months_left, 2)
            running = 0.0
            for i in range(months_left):
                line_date = self._depreciation_date_for(first_seq + i)
                if i == months_left - 1:
                    # Absorb rounding residual in the last line so total == base.
                    amount = round(remaining - running, 2)
                else:
                    amount = monthly
                    running += amount
                vals_list.append(
                    {
                        "asset_id": self.id,
                        "sequence": first_seq + i,
                        "date": line_date,
                        "amount": amount,
                    }
                )
        elif self.depreciation_method == "declining":
            # Declining balance: each month depreciate (factor / total_months)
            # of the remaining NBV. Switch to straight-line on the residual in
            # the final period to fully consume the base.
            rate = self.declining_factor / float(months)
            nbv = remaining
            running = 0.0
            for i in range(months_left):
                line_date = self._depreciation_date_for(first_seq + i)
                if i == months_left - 1:
                    amount = round(remaining - running, 2)
                else:
                    amount = round(nbv * rate, 2)
                    if running + amount > remaining:
                        amount = round(remaining - running, 2)
                    nbv -= amount
                    running += amount
                vals_list.append(
                    {
                        "asset_id": self.id,
                        "sequence": first_seq + i,
                        "date": line_date,
                        "amount": amount,
                    }
                )

        if vals_list:
            self.env["custom.fixed.asset.depreciation.line"].create(vals_list)

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------
    def action_confirm(self):
        for asset in self:
            if asset.state != "draft":
                raise UserError(_("Only draft assets can be confirmed."))
            if asset.depreciation_method != "none":
                if not asset.expense_account_id or not asset.depreciation_account_id:
                    raise UserError(
                        _(
                            'Asset "%(name)s": depreciation expense and accumulated '
                            "depreciation accounts must be set before confirming.",
                            name=asset.name,
                        )
                    )
                if not asset.journal_id:
                    raise UserError(
                        _(
                            'Asset "%(name)s": depreciation journal must be set.',
                            name=asset.name,
                        )
                    )
                asset._build_schedule()
            asset.state = "running"

    def action_cancel(self):
        for asset in self:
            if asset.depreciation_line_ids.filtered("posted"):
                raise UserError(
                    _(
                        'Cannot cancel asset "%(name)s": depreciation entries have '
                        "already been posted. Reverse them first.",
                        name=asset.name,
                    )
                )
        self.filtered(lambda a: a.state in ("draft", "running")).write(
            {
                "state": "cancelled",
            }
        )

    def action_reset_draft(self):
        for asset in self:
            if asset.depreciation_line_ids.filtered("posted"):
                raise UserError(
                    _(
                        'Asset "%(name)s" has posted depreciation; cannot reset.',
                        name=asset.name,
                    )
                )
        self.depreciation_line_ids.unlink()
        self.write({"state": "draft"})

    def action_open_dispose_wizard(self):
        self.ensure_one()
        if self.state != "running":
            raise UserError(_("Only running assets can be disposed."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Dispose Asset"),
            "res_model": "custom.fixed.asset.disposal.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_asset_id": self.id},
        }

    def action_open_revaluation_wizard(self):
        self.ensure_one()
        if self.state != "running":
            raise UserError(_("Only running assets can be revalued."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Revalue Asset"),
            "res_model": "custom.fixed.asset.revaluation.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_asset_id": self.id},
        }

    def action_view_revaluations(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Revaluations"),
            "res_model": "custom.fixed.asset.revaluation",
            "view_mode": "list,form",
            "domain": [("asset_id", "=", self.id)],
            "context": {"default_asset_id": self.id},
        }

    # ------------------------------------------------------------------
    # Posting due depreciation lines
    # ------------------------------------------------------------------
    def _group_depreciation_moves(self):
        """Whether to book ONE journal entry per period instead of one per asset.

        Default ON. A per-asset entry means 3,300+ documents a month on a real
        register (the tenant carries 3,328 running assets), which is not what
        Accounting asks for — they want a single monthly depreciation document
        they can point at, and the per-asset detail stays in this subledger.

        Set ``custom_accounting_asset.group_depreciation_moves`` to ``0`` on a
        tenant whose Accounting genuinely wants one entry per asset.
        """
        param = (
            self.env["ir.config_parameter"].sudo().get_param("custom_accounting_asset.group_depreciation_moves", "1")
        )
        return str(param).strip().lower() in ("1", "true", "yes")

    def _post_due_depreciation(self, as_of=None):
        """Post all unposted depreciation lines whose date is <= ``as_of``.

        Books DR expense / CR accumulated depreciation. Lines are grouped into
        one ``account.move`` per (company, journal, expense account, accumulated
        account, date) — so a monthly run yields one document per date rather
        than one per asset. See :meth:`_group_depreciation_moves` to opt out.

        Grouping keys on the exact line ``date`` rather than on the month, so
        assets whose schedule falls on a different day of the month keep their
        own correctly-dated entry instead of being pulled into someone else's
        accounting date.
        """
        as_of = as_of or fields.Date.context_today(self)
        AccountMove = self.env["account.move"]
        grouped = self._group_depreciation_moves()

        # Collect due lines across the whole recordset first: grouping has to
        # span assets, so this cannot be done inside a per-asset loop.
        buckets = {}
        for asset in self:
            if asset.state != "running":
                continue
            due = asset.depreciation_line_ids.filtered(lambda l: not l.posted and not l.reversed and l.date <= as_of)
            for line in due:
                key = (
                    asset.company_id.id,
                    asset.journal_id.id,
                    asset.expense_account_id.id,
                    asset.depreciation_account_id.id,
                    line.date,
                    # Without grouping, the line's own id keeps every bucket
                    # singular and restores the one-move-per-line behaviour.
                    None if grouped else line.id,
                )
                buckets.setdefault(key, self.env["custom.fixed.asset.depreciation.line"])
                buckets[key] |= line

        posted_count = 0
        for key in sorted(buckets, key=lambda k: (k[4], k[0], k[1])):
            company_id, journal_id, expense_id, accum_id, date, _singleton = key
            lines = buckets[key]
            total = sum(lines.mapped("amount"))
            if not total:
                continue

            if len(lines) == 1:
                asset = lines.asset_id
                ref = _(
                    "Depreciation %(code)s #%(seq)s",
                    code=asset.code,
                    seq=lines.sequence,
                )
                expense_label = _("Depreciation %(name)s", name=asset.name)
                accum_label = _("Accum. depreciation %(name)s", name=asset.name)
            else:
                ref = _(
                    "Depreciation %(date)s (%(count)s assets)",
                    date=date,
                    count=len(lines.asset_id),
                )
                expense_label = _("Depreciation %(date)s", date=date)
                accum_label = _("Accum. depreciation %(date)s", date=date)

            move = AccountMove.create(
                {
                    "date": date,
                    "journal_id": journal_id,
                    "company_id": company_id,
                    "ref": ref,
                    "line_ids": [
                        (
                            0,
                            0,
                            {
                                "name": expense_label,
                                "account_id": expense_id,
                                "debit": total,
                                "credit": 0.0,
                            },
                        ),
                        (
                            0,
                            0,
                            {
                                "name": accum_label,
                                "account_id": accum_id,
                                "debit": 0.0,
                                "credit": total,
                            },
                        ),
                    ],
                }
            )
            move.action_post()
            lines.write({"posted": True, "move_id": move.id})
            posted_count += len(lines)
        # If a schedule is fully consumed -> nothing else to do; the asset
        # remains running until explicitly disposed.
        return posted_count

    @api.model
    def _cron_post_due_depreciation(self):
        """Monthly cron entry point. Posts every running asset whose
        schedule has reached its due date.
        """
        running = self.search([("state", "=", "running")])
        count = running._post_due_depreciation()
        _logger.info("custom.fixed.asset: posted %s depreciation lines", count)
        return count

    def action_post_selected(self):
        """Bulk-post every due depreciation line (as of today) for the assets
        in ``self``. Wired to a list multi-select server action.
        """
        count = self._post_due_depreciation()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if count else "warning",
                "title": _("Depreciation Posting"),
                "message": _("%(count)s depreciation entr(y/ies) posted.", count=count),
                "sticky": False,
            },
        }

    # ------------------------------------------------------------------
    # Create with sequence
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("code", _("New")) == _("New"):
                seq = self.env["ir.sequence"].next_by_code("custom.fixed.asset")
                vals["code"] = seq or _("New")
            # Default the depreciation anchor to the acquisition date.
            if not vals.get("posting_date") and vals.get("acquisition_date"):
                vals["posting_date"] = vals["acquisition_date"]
        return super().create(vals_list)
