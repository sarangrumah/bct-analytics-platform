# -*- coding: utf-8 -*-
"""Per-store sales targets, prorated by day overlap.

A target is stated for a period (normally a calendar month) and a store. The
dashboard almost never asks about exactly that period: on the 12th of the month
it asks about the 1st..12th. Comparing a month-to-date actual against a
whole-month target is the single most common way a store report lies, so the
proration lives here, once, rather than in each consumer:

    contribution = target_amount * overlapping_days / period_days

Proration is by day, not by trading weight — a shopping mall does not sell the
same amount on a Tuesday as on a Saturday. ``spread_dow`` is deliberately not
modelled: nobody at the apparel brand maintains a day-of-week curve today, and a fabricated
one would make the pace line look precise while being invented. Day proration is
visibly approximate, which is the honest failure mode.
"""

from datetime import date, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class RetailSalesTarget(models.Model):
    _name = "retail.sales.target"
    _description = "the apparel brand Store Sales Target"
    _order = "date_from desc, warehouse_id"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Store",
        required=True,
        ondelete="cascade",
        index=True,
        check_company=True,
    )
    date_from = fields.Date(
        required=True,
        default=lambda self: date.today().replace(day=1),
        index=True,
    )
    date_to = fields.Date(
        required=True,
        default=lambda self: self._default_date_to(),
        index=True,
    )
    target_amount = fields.Monetary(
        string="Net Sales Target",
        required=True,
        help="Target net sales (excluding tax) for this store over the period.",
    )
    target_qty = fields.Float(
        string="Units Target",
        help="Optional. Target units sold; leave at 0 to track amount only.",
    )
    target_transactions = fields.Integer(
        string="Transactions Target",
        help="Optional. Target number of POS transactions; leave at 0 to track amount only.",
    )
    active = fields.Boolean(default=True)
    note = fields.Char(string="Note")

    day_count = fields.Integer(
        string="Days",
        compute="_compute_day_count",
        store=True,
        help="Calendar days in the period — the denominator used when a target is prorated.",
    )
    amount_per_day = fields.Monetary(
        string="Target / Day",
        compute="_compute_day_count",
        store=True,
    )

    _period_valid = models.Constraint(
        "CHECK (date_to >= date_from)",
        "A target's end date cannot be before its start date.",
    )
    _amount_positive = models.Constraint(
        "CHECK (target_amount >= 0)",
        "A sales target cannot be negative.",
    )

    # ------------------------------------------------------------------
    # Compute / constraints
    # ------------------------------------------------------------------
    @api.model
    def _default_date_to(self):
        first = date.today().replace(day=1)
        return (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)

    @api.depends("date_from", "date_to", "target_amount")
    def _compute_day_count(self):
        for rec in self:
            if rec.date_from and rec.date_to and rec.date_to >= rec.date_from:
                days = (rec.date_to - rec.date_from).days + 1
            else:
                days = 0
            rec.day_count = days
            rec.amount_per_day = (rec.target_amount / days) if days else 0.0

    @api.depends("warehouse_id", "date_from", "date_to")
    def _compute_display_name(self):
        for rec in self:
            store = rec.warehouse_id.name or _("Unassigned")
            if rec.date_from and rec.date_to:
                rec.display_name = "%s — %s → %s" % (store, rec.date_from, rec.date_to)
            else:
                rec.display_name = store

    @api.constrains("warehouse_id", "date_from", "date_to", "company_id", "active")
    def _check_no_overlap(self):
        """Two targets for the same store over the same day would double the goal.

        Only active records are checked: archiving is how a superseded target is
        retired without deleting the history behind an already-published number.
        """
        for rec in self:
            if not rec.active:
                continue
            clash = self.search(
                [
                    ("id", "!=", rec.id),
                    ("company_id", "=", rec.company_id.id),
                    ("warehouse_id", "=", rec.warehouse_id.id),
                    ("date_from", "<=", rec.date_to),
                    ("date_to", ">=", rec.date_from),
                ],
                limit=1,
            )
            if clash:
                raise ValidationError(
                    _(
                        "Store %(store)s already has a target covering %(start)s → %(end)s "
                        "(%(clash)s). Periods may not overlap.",
                        store=rec.warehouse_id.display_name,
                        start=rec.date_from,
                        end=rec.date_to,
                        clash=clash.display_name,
                    )
                )

    # ------------------------------------------------------------------
    # Public API — consumed by retail.sales.report.get_dashboard
    # ------------------------------------------------------------------
    @api.model
    def prorated_targets(self, date_from, date_to, warehouse_ids=None, company_ids=None):
        """Target contribution per store for the window ``date_from..date_to``.

        Returns ``{warehouse_id: {"amount", "qty", "transactions"}}``. A target
        that only partly overlaps the window contributes in proportion to the
        overlapping days, so month-to-date actuals meet a month-to-date target.
        Stores without a target are simply absent from the result — the caller
        distinguishes "no target set" (no attainment shown) from "target of 0"
        (attainment is infinite), which a defaulted 0.0 would collapse.
        """
        if not date_from or not date_to or date_to < date_from:
            return {}
        domain = [
            ("date_from", "<=", date_to),
            ("date_to", ">=", date_from),
            ("company_id", "in", list(company_ids or self.env.companies.ids)),
        ]
        if warehouse_ids:
            domain.append(("warehouse_id", "in", list(warehouse_ids)))

        out = {}
        for target in self.search(domain):
            days = target.day_count
            if not days:
                continue
            overlap_start = max(target.date_from, date_from)
            overlap_end = min(target.date_to, date_to)
            overlap = (overlap_end - overlap_start).days + 1
            if overlap <= 0:
                continue
            ratio = overlap / days
            bucket = out.setdefault(
                target.warehouse_id.id,
                {"amount": 0.0, "qty": 0.0, "transactions": 0.0},
            )
            bucket["amount"] += target.target_amount * ratio
            bucket["qty"] += target.target_qty * ratio
            bucket["transactions"] += target.target_transactions * ratio
        return out
