# -*- coding: utf-8 -*-
"""Putaway strategy header — groups ordered rules for a warehouse."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class WmsPutawayStrategy(models.Model):
    _name = "custom.wms.putaway.strategy"
    _description = "WMS Putaway Strategy"
    _inherit = ["mail.thread", "pdp.audited.mixin"]
    _order = "sequence, id"
    _check_company_auto = True

    name = fields.Char(required=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Warehouse",
        required=True,
        check_company=True,
        ondelete="cascade",
        index=True,
    )
    rule_set = fields.Selection(
        [
            ("zwme001_6tier", "ZWME001 6-Tier"),
            ("abc", "ABC Classification"),
            ("fefo", "FEFO (First-Expiry-First-Out Slotting)"),
            ("custom", "Custom"),
        ],
        required=True,
        default="zwme001_6tier",
        tracking=True,
    )
    auto_apply_suggestions = fields.Boolean(
        string="Auto-apply suggestion",
        default=False,
        help="If set, the engine will rewrite the inbound move line "
        "destination automatically without operator confirmation.",
    )
    rule_ids = fields.One2many(
        "custom.wms.putaway.rule",
        "strategy_id",
        string="Rules",
    )
    rule_count = fields.Integer(compute="_compute_rule_count")
    suggestion_count = fields.Integer(compute="_compute_suggestion_count")

    # Odoo 19 SILENTLY IGNORES ``_sql_constraints`` — it logs a warning and
    # creates nothing. The declaration below is the supported form and does
    # reach PostgreSQL. (Verify with: select conname from pg_constraint where
    # conname like '%active_strategy%';)
    _uniq_active_strategy_warehouse = models.Constraint(
        "EXCLUDE (warehouse_id WITH =) WHERE (active)",
        "Only one active putaway strategy is allowed per warehouse.",
    )

    @api.depends("rule_ids")
    def _compute_rule_count(self):
        for rec in self:
            rec.rule_count = len(rec.rule_ids)

    def _compute_suggestion_count(self):
        Suggestion = self.env["custom.wms.putaway.suggestion"]
        for rec in self:
            rec.suggestion_count = Suggestion.search_count([("rule_id.strategy_id", "=", rec.id)])

    @api.constrains("rule_ids", "rule_set")
    def _check_zwme001_tiers(self):
        for rec in self:
            if rec.rule_set != "zwme001_6tier":
                continue
            tiers = rec.rule_ids.mapped("tier")
            for t in tiers:
                if not 1 <= t <= 6:
                    raise ValidationError(_("ZWME001 strategies require tier in [1,6]; got %s.") % t)

    def action_view_rules(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Rules"),
            "res_model": "custom.wms.putaway.rule",
            "view_mode": "list,form",
            "domain": [("strategy_id", "=", self.id)],
            "context": {"default_strategy_id": self.id},
        }

    def action_view_suggestions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Suggestions"),
            "res_model": "custom.wms.putaway.suggestion",
            "view_mode": "list,form",
            "domain": [("rule_id.strategy_id", "=", self.id)],
        }

    # -- engine entrypoint ------------------------------------------------

    def _suggest_putaway(self, move_line):
        """Evaluate this strategy against a move line and return a suggestion record (or False).

        Tier order is respected: lowest tier wins; rules within a tier are
        scored and the best one above threshold wins.
        """
        self.ensure_one()
        if not move_line or not move_line.product_id:
            return False
        engine = self.env["custom.putaway.engine"]
        active_rules = self.rule_ids.filtered(lambda r: r.active).sorted(key=lambda r: (r.tier, r.sequence))

        best_rule = None
        best_score = -1
        best_reason = ""
        best_location = None
        for rule in active_rules:
            # Strict tier ordering: once a tier has produced a winner, a rule
            # from a later (numerically higher) tier can never displace it.
            if best_rule is not None and rule.tier > best_rule.tier:
                break
            score, reason, chosen = rule._evaluate(move_line)
            if score <= 0:
                continue
            location = chosen or rule.target_location_id
            if location:
                location = engine._feasible_locations(location, move_line)
            if not location:
                continue
            if score > best_score:
                best_rule, best_score, best_reason, best_location = rule, score, reason, location[:1]
        if not best_rule or not best_location:
            return False
        Suggestion = self.env["custom.wms.putaway.suggestion"]
        suggestion = Suggestion.create(
            {
                "picking_id": move_line.picking_id.id,
                "move_line_id": move_line.id,
                "original_dest_location_id": move_line.location_dest_id.id,
                "suggested_location_id": best_location.id,
                "rule_id": best_rule.id,
                "score": best_score,
                "reason": best_reason,
            }
        )
        if self.auto_apply_suggestions or best_score >= engine._auto_apply_threshold():
            suggestion.action_apply()
        return suggestion
