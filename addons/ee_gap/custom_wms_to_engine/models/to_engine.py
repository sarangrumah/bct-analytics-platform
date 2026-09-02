# -*- coding: utf-8 -*-
"""Transfer Order engine — rule evaluation + materialization service.

Produces proposal dicts based on trigger semantics, then materializes them
into ``custom.transfer.order`` records (and backing ``stock.move`` entries)
when ``materialize`` is invoked. Rules are evaluated in priority order.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ToEngine(models.AbstractModel):
    _name = "custom.to.engine"
    _description = "Transfer Order Engine"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @api.model
    def evaluate_all(self):
        """Loop all active rules in priority order, return aggregated proposals."""
        Rule = self.env["custom.to.rule"]
        rules = Rule.search([("active", "=", True)], order="priority asc, sequence asc")
        out = []
        for rule in rules:
            try:
                out.extend(self.evaluate_rule(rule))
            except Exception as exc:  # pragma: no cover - defensive
                _logger.warning("TO rule %s eval failed: %s", rule.id, exc)
        return out

    @api.model
    def evaluate_rule(self, rule):
        """Dispatch to a per-trigger evaluator returning proposal dicts."""
        handler = getattr(self, f"_eval_{rule.trigger}", None)
        if not handler:
            return []
        return handler(rule) or []

    # ------------------------------------------------------------------
    # Trigger evaluators
    # ------------------------------------------------------------------

    def _eval_low_water_mark(self, rule):
        """Low-water mark: source quants below threshold + target has stock to push from."""
        Quant = self.env["stock.quant"]
        src_dom = [("location_id.usage", "=", "internal")] + rule._eval_domain(rule.source_location_domain)
        tgt_dom = [("location_id.usage", "=", "internal")] + rule._eval_domain(rule.target_location_domain)
        threshold = rule.low_water_qty or 0.0
        proposals = []
        seen_keys = set()
        for q in Quant.search(src_dom):
            if (q.quantity or 0.0) >= threshold:
                continue
            key = (q.product_id.id, q.location_id.id)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            # Find a donor in the target domain that has stock of the same
            # product. The donor must be a *different* bin — the domains can
            # legitimately overlap (a bin that is both pickable and stocked),
            # and a transfer from a bin to itself replenishes nothing.
            donor = Quant.search(
                tgt_dom
                + [
                    ("product_id", "=", q.product_id.id),
                    ("quantity", ">", 0),
                    ("location_id", "!=", q.location_id.id),
                ],
                limit=1,
            )
            if not donor:
                continue
            needed = max(0.0, threshold - (q.quantity or 0.0))
            proposals.append(
                {
                    "rule_id": rule.id,
                    "source_location_id": donor.location_id.id,
                    "target_location_id": q.location_id.id,
                    "product_id": q.product_id.id,
                    "lot_id": donor.lot_id.id if donor.lot_id else False,
                    "planned_qty": min(needed, donor.quantity or 0.0),
                    "reason": "low_water_mark",
                }
            )
        return proposals

    def _eval_expiry_approaching(self, rule):
        """Lots whose expiry < today+window get routed to a scrap location."""
        Lot = self.env["stock.lot"]
        Loc = self.env["stock.location"]
        Quant = self.env["stock.quant"]
        if not hasattr(Lot, "expiration_date"):
            return []
        window = max(0, rule.expiry_days_ahead or 7)
        cutoff = fields.Datetime.now() + timedelta(days=window)
        # Odoo 19 removed stock.location.scrap_location; core now resolves the
        # scrap destination as the company's first usage='inventory' location
        # (see stock.scrap._compute_scrap_location_id). Searching the old field
        # raises KeyError and takes the whole rule evaluation down with it.
        scrap_domain = [("usage", "=", "inventory")]
        if rule.company_id:
            scrap_domain.append(("company_id", "in", (False, rule.company_id.id)))
        scrap = Loc.search(scrap_domain, order="id", limit=1)
        if not scrap:
            return []
        src_dom = rule._eval_domain(rule.source_location_domain)
        lots = Lot.search([("expiration_date", "<=", cutoff)])
        proposals = []
        for lot in lots:
            q = Quant.search(
                [("lot_id", "=", lot.id), ("quantity", ">", 0)] + src_dom,
                limit=1,
            )
            if not q:
                continue
            proposals.append(
                {
                    "rule_id": rule.id,
                    "source_location_id": q.location_id.id,
                    "target_location_id": scrap.id,
                    "product_id": lot.product_id.id,
                    "lot_id": lot.id,
                    "planned_qty": q.quantity or 0.0,
                    "reason": "expiry_approaching",
                }
            )
        return proposals

    def _eval_zone_consolidation(self, rule):
        """Products with only a half-bin scrap in source where target has space."""
        Quant = self.env["stock.quant"]
        src_dom = [("location_id.usage", "=", "internal")] + rule._eval_domain(rule.source_location_domain)
        tgt_dom = [("location_id.usage", "=", "internal")] + rule._eval_domain(rule.target_location_domain)
        proposals = []
        for q in Quant.search(src_dom):
            if (q.quantity or 0.0) <= 0 or q.quantity > 1.0:
                continue
            home = Quant.search(
                tgt_dom + [("product_id", "=", q.product_id.id), ("quantity", ">", 0)],
                limit=1,
            )
            if not home or home.location_id == q.location_id:
                continue
            proposals.append(
                {
                    "rule_id": rule.id,
                    "source_location_id": q.location_id.id,
                    "target_location_id": home.location_id.id,
                    "product_id": q.product_id.id,
                    "lot_id": q.lot_id.id if q.lot_id else False,
                    "planned_qty": q.quantity,
                    "reason": "zone_consolidation",
                }
            )
        return proposals

    def _eval_picking_replenishment(self, rule):
        """For pickings confirmed in next 24h, pre-pick to staging."""
        Picking = self.env["stock.picking"]
        horizon = fields.Datetime.now() + timedelta(hours=24)
        pickings = Picking.search(
            [
                ("state", "in", ("confirmed", "assigned")),
                ("scheduled_date", "<=", horizon),
            ]
        )
        proposals = []
        tgt_dom = rule._eval_domain(rule.target_location_domain)
        Loc = self.env["stock.location"]
        staging = Loc.search(tgt_dom, limit=1) if tgt_dom else False
        for pick in pickings:
            for move in pick.move_ids:
                if not staging:
                    continue
                proposals.append(
                    {
                        "rule_id": rule.id,
                        "source_location_id": move.location_id.id,
                        "target_location_id": staging.id,
                        "product_id": move.product_id.id,
                        "planned_qty": move.product_uom_qty,
                        "reason": "picking_replenishment",
                    }
                )
        return proposals

    def _eval_manual(self, rule):
        # Manual rules produce no proposals — they're materialized via wizard.
        return []

    # ------------------------------------------------------------------
    # Materialization
    # ------------------------------------------------------------------

    @api.model
    def materialize(self, proposal_dict, transfer_order=None):
        """Materialize a proposal dict into a ``stock.move`` (and TO if not provided)."""
        TO = self.env["custom.transfer.order"]
        if transfer_order is None:
            to_vals = {
                "rule_id": proposal_dict.get("rule_id"),
                "source_location_id": proposal_dict["source_location_id"],
                "target_location_id": proposal_dict["target_location_id"],
                "product_id": proposal_dict["product_id"],
                "lot_id": proposal_dict.get("lot_id") or False,
                "planned_qty": proposal_dict.get("planned_qty", 0.0),
            }
            transfer_order = TO.create(to_vals)
        product = self.env["product.product"].browse(proposal_dict["product_id"])
        move = self.env["stock.move"].create(
            {
                "origin": proposal_dict.get("name") or transfer_order.name,
                "product_id": product.id,
                "product_uom": product.uom_id.id,
                "product_uom_qty": proposal_dict.get("planned_qty", 0.0),
                "location_id": proposal_dict["source_location_id"],
                "location_dest_id": proposal_dict["target_location_id"],
                "company_id": proposal_dict.get("company_id") or self.env.company.id,
            }
        )
        transfer_order.stock_move_id = move.id
        if transfer_order.state == "draft":
            transfer_order.state = "proposed"
        return move

    # ------------------------------------------------------------------
    # Cron entrypoint
    # ------------------------------------------------------------------

    @api.model
    def cron_evaluate_and_materialize(self):
        """Cron: eval all rules and materialize each proposal as a TO."""
        proposals = self.evaluate_all()
        TO = self.env["custom.transfer.order"]
        created = TO
        for p in proposals:
            try:
                vals = {
                    "rule_id": p.get("rule_id"),
                    "source_location_id": p["source_location_id"],
                    "target_location_id": p["target_location_id"],
                    "product_id": p["product_id"],
                    "lot_id": p.get("lot_id") or False,
                    "planned_qty": p.get("planned_qty", 0.0),
                    "state": "proposed",
                }
                created |= TO.create(vals)
            except UserError as exc:  # pragma: no cover - defensive
                _logger.warning("TO materialize skipped: %s", exc)
        return created
