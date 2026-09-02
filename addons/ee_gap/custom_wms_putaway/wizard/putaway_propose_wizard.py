# -*- coding: utf-8 -*-
"""Bulk re-propose putaway suggestions for an entire picking."""

from __future__ import annotations

from odoo import _, fields, models
from odoo.exceptions import UserError


class PutawayProposeWizard(models.TransientModel):
    _name = "custom.wms.putaway.propose.wizard"
    _description = "Putaway Bulk Re-Propose Wizard"

    picking_id = fields.Many2one("stock.picking", required=True)
    only_pending = fields.Boolean(default=True, help="Skip move lines already at their target.")
    auto_apply_high_confidence = fields.Boolean(default=True)

    def action_propose(self):
        self.ensure_one()
        if not self.picking_id:
            raise UserError(_("Pick a picking first."))
        engine = self.env["custom.putaway.engine"]
        created = self.env["custom.wms.putaway.suggestion"]
        for ml in self.picking_id.move_line_ids:
            sugg = engine.apply_top_proposal(ml)
            if sugg:
                created |= sugg
        return {
            "type": "ir.actions.act_window",
            "name": _("Suggestions"),
            "res_model": "custom.wms.putaway.suggestion",
            "view_mode": "list,form",
            "domain": [("id", "in", created.ids)],
        }
