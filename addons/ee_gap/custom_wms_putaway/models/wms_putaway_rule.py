# -*- coding: utf-8 -*-
"""Putaway rule — a single tiered scoring entry within a strategy.

Domain Char fields (e.g. ``product_domain``, ``target_location_domain``) are
stored as text and evaluated with ``safe_eval`` at runtime. This pattern is
used (rather than a true Domain widget) so that admin users can persist
arbitrary product/location selection expressions without server restart, and
so the engine remains side-effect free during evaluation.
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval


_STRATEGY_KINDS = [
    ("fixed_location", "Fixed Location"),
    ("nearest_empty", "Nearest Empty"),
    ("zone_round_robin", "Zone Round-Robin"),
    ("by_volume", "By Volume Fit"),
    ("by_dimension", "By Dimension Fit (PxLxT)"),
    ("by_weight", "By Weight Headroom"),
    ("by_temperature", "By Temperature"),
    ("by_abc_velocity", "By ABC Velocity"),
    ("custom_python", "Custom Python (safe_eval)"),
]


class WmsPutawayRule(models.Model):
    _name = "custom.wms.putaway.rule"
    _description = "WMS Putaway Rule"
    _inherit = ["mail.thread", "pdp.audited.mixin"]
    _order = "strategy_id, tier, sequence, id"
    _check_company_auto = True

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    tier = fields.Integer(default=1, required=True, help="Lower = higher priority (1..6).")
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    strategy_id = fields.Many2one(
        "custom.wms.putaway.strategy",
        required=True,
        ondelete="cascade",
        check_company=True,
        index=True,
    )
    kind = fields.Selection(_STRATEGY_KINDS, required=True, default="fixed_location", tracking=True)

    # Targeting -----------------------------------------------------------
    target_location_id = fields.Many2one(
        "stock.location",
        string="Target Location",
        check_company=True,
    )
    target_location_domain = fields.Char(
        string="Target Location Domain",
        help="Optional Odoo domain (text) evaluated via safe_eval to constrain "
        "candidate locations. Example: [('usage','=','internal')]",
    )
    product_domain = fields.Char(
        string="Product Domain",
        help="Optional Odoo domain (text) restricting which products this rule applies to.",
    )
    abc_class = fields.Selection(
        [("A", "A"), ("B", "B"), ("C", "C")],
        string="ABC Class Filter",
    )
    temperature_zone = fields.Selection(
        [("ambient", "Ambient"), ("chilled", "Chilled"), ("frozen", "Frozen")],
    )
    product_categ_ids = fields.Many2many(
        "product.category",
        "custom_wms_putaway_rule_categ_rel",
        "rule_id",
        "categ_id",
        string="Product Categories",
        help="Declarative alternative to Product Domain: the rule applies only "
        "to these categories and their children. Cheaper and safer than a "
        "safe_eval domain string.",
    )
    dock_location_id = fields.Many2one(
        "stock.location",
        string="Dock / Origin",
        check_company=True,
        help="Reference point for 'nearest empty' distance scoring. Defaults to "
        "the move line's source location when unset.",
    )
    round_robin_cursor = fields.Integer(
        default=0,
        copy=False,
        readonly=True,
        help="Internal rotation counter for the zone round-robin kind.",
    )

    # Custom python expression (safe_eval) --------------------------------
    custom_python = fields.Text(
        string="Custom Python Expression",
        help="Expression evaluated via safe_eval with locals: move_line, product, "
        "candidate_locations, env. Must return a tuple (location_id_or_False, score_int_0_100).",
    )

    # Scoring weights -----------------------------------------------------
    weight_volume = fields.Float(default=0.30)
    weight_distance = fields.Float(default=0.30)
    weight_age = fields.Float(default=0.10)
    weight_abc = fields.Float(default=0.30)

    @api.constrains("tier")
    def _check_tier(self):
        for rec in self:
            if rec.tier <= 0:
                raise ValidationError(_("Tier must be a positive integer."))

    # -- domain helpers ---------------------------------------------------

    def _eval_domain(self, raw: str | None) -> list:
        if not raw:
            return []
        try:
            # Odoo 19 signature: safe_eval(expr, /, context=None, *, mode, filename).
            # Passing a third positional (the old `locals_dict`) raises TypeError,
            # which _score_rule used to swallow — every domain-driven rule scored
            # 0 and no one noticed. Do not "restore" the old call shape.
            value = safe_eval(raw)
        except Exception as exc:
            raise ValidationError(_("Invalid domain expression: %s") % exc) from exc
        if not isinstance(value, list):
            raise ValidationError(_("Domain must evaluate to a list."))
        return value

    # -- engine helpers ---------------------------------------------------

    def _candidate_locations(self):
        """Internal bins this rule may target, before the engine's hard gate.

        Note this deliberately does NOT apply capacity / category feasibility —
        that lives in ``custom.putaway.engine._feasible_locations`` so every
        scoring kind gets the same treatment and the rule stays a pure filter.
        """
        self.ensure_one()
        Location = self.env["stock.location"]
        domain = [("usage", "=", "internal")]
        if self.company_id:
            domain.append(("company_id", "in", (False, self.company_id.id)))
        domain += self._eval_domain(self.target_location_domain)
        return Location.search(domain)

    def _dock_location(self):
        """Origin used for distance scoring; empty recordset when unset."""
        self.ensure_one()
        return self.dock_location_id

    def _next_round_robin_index(self, length: int) -> int:
        """Advance and return the rotation cursor, wrapped to ``length``.

        Written with ``sudo()`` because operators scanning at the handheld are
        not expected to hold write access on putaway configuration.
        """
        self.ensure_one()
        if length <= 0:
            return 0
        cursor = (self.round_robin_cursor or 0) % length
        self.sudo().round_robin_cursor = (cursor + 1) % length
        return cursor

    def _matches_product(self, product) -> bool:
        self.ensure_one()
        if self.abc_class and getattr(product, "abc_class", False) != self.abc_class:
            return False
        if self.product_categ_ids:
            tree = self.env["product.category"].search([("id", "child_of", self.product_categ_ids.ids)])
            if product.categ_id.id not in tree.ids:
                return False
        if self.product_domain:
            dom = self._eval_domain(self.product_domain)
            return bool(self.env["product.product"].search_count([("id", "=", product.id)] + dom))
        return True

    def _evaluate(self, move_line):
        """Return ``(score:int 0..100, reason:str, location|None)``. 0 = no match."""
        self.ensure_one()
        product = move_line.product_id
        if not self._matches_product(product):
            return 0, "", None
        engine = self.env["custom.putaway.engine"]
        return engine._score_rule(self, move_line)
