# -*- coding: utf-8 -*-
"""Cycle-count plan — defines cadence, scope and sampling method."""

from __future__ import annotations

from datetime import timedelta

from odoo import api, fields, models


FREQUENCY = [
    ("daily", "Daily"),
    ("weekly", "Weekly"),
    ("monthly", "Monthly"),
    ("quarterly", "Quarterly"),
    ("adhoc", "Ad-hoc"),
]

METHOD = [
    ("abc_velocity", "ABC Velocity"),
    ("random", "Random"),
    ("by_zone", "By Zone"),
    ("by_value", "By Value"),
    ("last_counted", "Last Counted"),
]


class CycleCountPlan(models.Model):
    _name = "custom.cycle.count.plan"
    _description = "Cycle Count Plan"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    warehouse_id = fields.Many2one("stock.warehouse", required=True, index=True)
    frequency = fields.Selection(FREQUENCY, default="weekly", required=True, tracking=True)
    method = fields.Selection(METHOD, default="abc_velocity", required=True, tracking=True)
    scope_zone_ids = fields.Many2many(
        "stock.location",
        string="Scope Locations/Zones",
        domain=[("usage", "=", "internal")],
    )
    target_count_per_period = fields.Integer(default=50)
    next_run_date = fields.Date(default=fields.Date.context_today)
    state = fields.Selection(
        [("active", "Active"), ("paused", "Paused")],
        default="active",
        tracking=True,
    )
    session_ids = fields.One2many("custom.cycle.count.session", "plan_id")
    coverage_pct = fields.Float(compute="_compute_coverage", store=False)

    @api.depends("session_ids", "session_ids.state", "session_ids.line_count")
    def _compute_coverage(self):
        for rec in self:
            total = sum(rec.session_ids.mapped("line_count") or [0])
            target = max(rec.target_count_per_period, 1)
            rec.coverage_pct = min(100.0, 100.0 * total / target)

    def _advance_next_run(self):
        """Advance ``next_run_date`` based on ``frequency``."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        delta = {
            "daily": timedelta(days=1),
            "weekly": timedelta(days=7),
            "monthly": timedelta(days=30),
            "quarterly": timedelta(days=90),
            "adhoc": timedelta(days=0),
        }.get(self.frequency, timedelta(days=7))
        self.next_run_date = (self.next_run_date or today) + delta

    @api.model
    def _cron_generate_sessions(self):
        today = fields.Date.context_today(self)
        due = self.search(
            [
                ("state", "=", "active"),
                ("active", "=", True),
                ("next_run_date", "<=", today),
                ("frequency", "!=", "adhoc"),
            ]
        )
        Wizard = self.env["custom.cycle.count.start.wizard"]
        for plan in due:
            wiz = Wizard.create({"plan_id": plan.id})
            wiz.action_start()
            plan._advance_next_run()
        return True
