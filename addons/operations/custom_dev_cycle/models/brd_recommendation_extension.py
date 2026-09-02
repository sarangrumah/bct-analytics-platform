# -*- coding: utf-8 -*-
"""Extend brd.recommendation with dev cycle smart-button + O2M."""

from __future__ import annotations

from odoo import _, api, fields, models


class BrdRecommendation(models.Model):
    _inherit = "brd.recommendation"

    dev_cycle_ids = fields.One2many(
        "dev.cycle",
        "brd_recommendation_id",
        string="Dev Cycles",
    )
    dev_cycle_count = fields.Integer(compute="_compute_dev_cycle_count")

    @api.depends("dev_cycle_ids")
    def _compute_dev_cycle_count(self):
        for rec in self:
            rec.dev_cycle_count = len(rec.dev_cycle_ids)

    def action_create_dev_cycle(self):
        self.ensure_one()
        cycle = self.env["dev.cycle"].create(
            {
                "name": _("Dev Cycle: %s") % (self.name or self.display_name),
                "brd_recommendation_id": self.id,
                "assignee_id": self.assigned_user_id.id or False,
                "estimate_md": float(self.estimated_md or 0),
            }
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Dev Cycle"),
            "res_model": "dev.cycle",
            "res_id": cycle.id,
            "view_mode": "form",
        }

    def action_open_dev_cycles(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Dev Cycles"),
            "res_model": "dev.cycle",
            "view_mode": "list,form",
            "domain": [("brd_recommendation_id", "=", self.id)],
            "context": {"default_brd_recommendation_id": self.id},
        }
