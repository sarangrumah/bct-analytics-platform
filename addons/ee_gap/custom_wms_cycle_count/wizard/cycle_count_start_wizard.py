# -*- coding: utf-8 -*-
"""Wizard that materializes a cycle-count session + seeded lines from a plan.

Uses bulk ``create([...])`` for line generation to keep 1000+ line sessions
fast.
"""

from __future__ import annotations

import random

from odoo import _, fields, models
from odoo.exceptions import UserError


class CycleCountStartWizard(models.TransientModel):
    _name = "custom.cycle.count.start.wizard"
    _description = "Cycle Count Start Wizard"

    plan_id = fields.Many2one("custom.cycle.count.plan", required=True)
    user_ids = fields.Many2many("res.users", string="Assigned Counters")
    scheduled_date = fields.Date(default=fields.Date.context_today)
    target_count = fields.Integer(default=0, help="Override plan target if > 0.")

    def _build_seed_lines(self, plan, limit: int):
        """Return list of vals dicts respecting the plan's sampling method."""
        Quant = self.env["stock.quant"]
        domain = [("location_id.usage", "=", "internal")]
        if plan.scope_zone_ids:
            domain.append(("location_id", "child_of", plan.scope_zone_ids.ids))
        if plan.warehouse_id and plan.warehouse_id.view_location_id:
            domain.append(("location_id", "child_of", plan.warehouse_id.view_location_id.id))
        quants = Quant.search(domain)
        if not quants:
            return []

        method = plan.method
        if method == "abc_velocity":
            quants = quants.sorted(
                key=lambda q: (
                    {"A": 0, "B": 1, "C": 2}.get(getattr(q.product_id, "abc_class", "B") or "B", 1),
                    -(q.quantity or 0.0),
                )
            )
            quants = quants[:limit]
        elif method == "random":
            ids = random.sample(quants.ids, k=min(limit, len(quants)))
            quants = Quant.browse(ids)
        elif method == "by_zone":
            # Already scoped; just trim.
            quants = quants[:limit]
        elif method == "by_value":
            quants = quants.sorted(key=lambda q: -((q.product_id.standard_price or 0.0) * (q.quantity or 0.0)))[:limit]
        elif method == "last_counted":
            # Sort by last counted ascending; quants without history come first.
            Line = self.env["custom.cycle.count.line"]
            last_map = {}
            history = Line.search_read(
                [("product_id", "in", quants.product_id.ids), ("counted_at", "!=", False)],
                ["product_id", "location_id", "counted_at"],
                order="counted_at desc",
            )
            for h in history:
                key = (h["product_id"][0], h["location_id"][0])
                last_map.setdefault(key, h["counted_at"])
            quants = quants.sorted(
                key=lambda q: (
                    last_map.get((q.product_id.id, q.location_id.id))
                    or fields.Datetime.from_string("1970-01-01 00:00:00")
                )
            )[:limit]
        else:
            quants = quants[:limit]

        vals_list = []
        for seq, q in enumerate(quants, start=1):
            vals_list.append(
                {
                    "sequence": seq * 10,
                    "location_id": q.location_id.id,
                    "product_id": q.product_id.id,
                    "lot_id": q.lot_id.id if q.lot_id else False,
                    "expected_qty": q.quantity or 0.0,
                }
            )
        return vals_list

    def action_start(self):
        self.ensure_one()
        plan = self.plan_id
        if not plan:
            raise UserError(_("Plan is required."))
        limit = self.target_count or plan.target_count_per_period or 50
        Session = self.env["custom.cycle.count.session"]
        session = Session.create(
            {
                "plan_id": plan.id,
                "scheduled_date": self.scheduled_date or fields.Date.context_today(self),
                "assigned_user_ids": [(6, 0, self.user_ids.ids)],
                "company_id": plan.company_id.id,
            }
        )
        seed = self._build_seed_lines(plan, limit)
        for v in seed:
            v["session_id"] = session.id
        if seed:
            # Bulk create — single round-trip for 1000+ rows.
            self.env["custom.cycle.count.line"].create(seed)
        return {
            "type": "ir.actions.act_window",
            "name": _("Cycle Count Session"),
            "res_model": "custom.cycle.count.session",
            "res_id": session.id,
            "view_mode": "form",
        }
