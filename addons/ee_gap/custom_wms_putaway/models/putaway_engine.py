# -*- coding: utf-8 -*-
"""Putaway engine — abstract scoring/proposal service.

This service produces candidate locations + confidence scores. It is invoked
either by the per-strategy ``_suggest_putaway`` flow or directly through
``propose(move_line)`` to return a ranked list.

Scoring contract
----------------
Every ``_score_<kind>(rule, move_line)`` handler returns either

    ``(score:int, reason:str)``                  -- location taken from the rule
    ``(score:int, reason:str, location:record)`` -- handler chose the location

``_score_rule`` normalises both shapes to a 3-tuple. Handlers that genuinely
select among candidates (nearest-empty, volume fit, dimension fit) MUST use the
3-tuple form, otherwise the chosen bin is discarded and the rule's static
``target_location_id`` is used instead.

Feasibility vs. preference
--------------------------
``_feasible_locations`` is a hard gate applied to every candidate set: category
reservation, weight ceiling and bin geometry are *constraints*, not scores. A
bin that cannot physically or contractually take the goods is removed before
any handler ranks anything.
"""

from __future__ import annotations

import logging

from odoo import _, api, models
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)

#: ``ir.config_parameter`` holding the auto-apply confidence threshold.
AUTO_APPLY_PARAM = "custom_wms_putaway.auto_apply_threshold"
DEFAULT_AUTO_APPLY_THRESHOLD = 90


class PutawayEngine(models.AbstractModel):
    _name = "custom.putaway.engine"
    _description = "WMS Putaway Engine"

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @api.model
    def _auto_apply_threshold(self) -> int:
        """Confidence above which a suggestion is applied without an operator.

        Configurable through ``ir.config_parameter``; an unparseable value
        falls back to the shipped default rather than blocking putaway.
        """
        raw = self.env["ir.config_parameter"].sudo().get_param(AUTO_APPLY_PARAM)
        if raw in (None, False, ""):
            return DEFAULT_AUTO_APPLY_THRESHOLD
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            _logger.warning(
                "%s is not numeric (%r); using default %s",
                AUTO_APPLY_PARAM,
                raw,
                DEFAULT_AUTO_APPLY_THRESHOLD,
            )
            return DEFAULT_AUTO_APPLY_THRESHOLD

    # ------------------------------------------------------------------
    # Feasibility gate
    # ------------------------------------------------------------------

    @api.model
    def _move_line_qty(self, move_line) -> float:
        """Quantity being put away, tolerant of the Odoo 19 field renames."""
        for fname in ("quantity", "quantity_product_uom", "reserved_uom_qty"):
            if fname in move_line._fields:
                value = move_line[fname]
                if value:
                    return float(value)
        return 0.0

    @api.model
    def _feasible_locations(self, locations, move_line):
        """Drop bins that cannot take this move line.

        Hard constraints, in order of cheapness:
          1. product-category reservation on the bin;
          2. weight ceiling (native storage category, else the bin fallback);
          3. bin geometry vs. handling-unit PxLxT (only when both are known).
        """
        if not locations:
            return locations
        product = move_line.product_id
        qty = self._move_line_qty(move_line)
        gross_kg = product._wms_gross_weight_kg(qty, move_line=move_line)
        pkg_dims = product._wms_package_dims_mm(move_line=move_line)

        keep = locations.browse()
        for loc in locations:
            if not loc._wms_accepts_category(product.categ_id):
                continue
            if gross_kg > loc._wms_free_weight_kg():
                continue
            if not self._fits_dimensions(loc, pkg_dims):
                continue
            keep |= loc
        return keep

    @api.model
    def _fits_dimensions(self, location, pkg_dims) -> bool:
        """True when a handling unit of ``pkg_dims`` fits the bin opening.

        Unknown geometry on either side is *permissive* — dimensions are an
        optional refinement, so a warehouse that has not captured them keeps
        the pre-dimension behaviour. The package may be rotated in the
        horizontal plane, so length/width are compared as a sorted pair.
        """
        bin_dims = location._wms_dims_mm()
        if not any(bin_dims) or not any(pkg_dims):
            return True
        bl, bw, bh = bin_dims
        pl, pw, ph = pkg_dims
        if bh and ph and ph > bh:
            return False
        if not (bl and bw) or not (pl and pw):
            return True
        bin_floor = sorted((bl, bw))
        pkg_floor = sorted((pl, pw))
        return pkg_floor[0] <= bin_floor[0] and pkg_floor[1] <= bin_floor[1]

    @api.model
    def _rule_candidates(self, rule, move_line):
        """Feasible bins for a rule, honouring the narrower of its two targets.

        A rule may be pinned to one bin (``target_location_id``) or scoped by a
        domain (``target_location_domain``). The domain wins when both are set;
        with neither, the whole internal tree is in play. Resolving this in one
        place matters: an earlier revision let a pinned rule silently widen to
        every internal location because an empty domain still searches all bins.
        """
        if rule.target_location_domain:
            base = rule._candidate_locations()
        elif rule.target_location_id:
            base = rule.target_location_id
        else:
            base = rule._candidate_locations()
        return self._feasible_locations(base, move_line)

    @api.model
    def _empty_locations(self, locations):
        """Subset of ``locations`` holding no positive stock, in one query."""
        if not locations:
            return locations
        groups = self.env["stock.quant"]._read_group(
            [("location_id", "in", locations.ids)],
            groupby=["location_id"],
            aggregates=["quantity:sum"],
        )
        occupied = {loc.id for loc, total in groups if (total or 0.0) > 0.0}
        return locations.filtered(lambda loc: loc.id not in occupied)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @api.model
    def propose(self, move_line):
        """Return a ranked list of dicts:
        [{'location_id': int, 'score': int, 'rule_id': int|False, 'reason': str}, ...]
        """
        if not move_line or not move_line.product_id:
            return []
        return self._propose(move_line, move_line.picking_id.picking_type_id.warehouse_id)

    @api.model
    def propose_for_product(self, product, warehouse, quantity=1.0, from_location=None):
        """Rank bins for a bare product, with no move line behind it.

        The handheld's stock-check screen asks "where would this go?" about a
        product the operator has merely scanned — there is no transfer, so
        ``propose`` has no move line to read the warehouse or quantity from.
        Scoring itself is unchanged: it runs against an in-memory move line, so
        a rule that inspects ``location_id`` or the put-away quantity behaves
        exactly as it would during a real receipt.
        """
        if not product or not warehouse:
            return []
        move_line = self.env["stock.move.line"].new(
            {
                "product_id": product.id,
                "quantity": quantity,
                "location_id": (from_location or warehouse.lot_stock_id).id,
                "location_dest_id": warehouse.lot_stock_id.id,
            }
        )
        return self._propose(move_line, warehouse)

    @api.model
    def _propose(self, move_line, warehouse):
        """Shared ranking body — ``warehouse`` is passed in because a scanned
        product has no picking to derive it from."""
        if not move_line or not move_line.product_id:
            return []
        Strategy = self.env["custom.wms.putaway.strategy"]
        strategies = Strategy.search([("active", "=", True), ("warehouse_id", "=", warehouse.id)])
        proposals: list[dict] = []
        for strategy in strategies:
            for rule in strategy.rule_ids.filtered(lambda r: r.active).sorted(key=lambda r: (r.tier, r.sequence)):
                if not rule._matches_product(move_line.product_id):
                    continue
                score, reason, chosen = self._score_rule(rule, move_line)
                if score <= 0:
                    continue
                # A handler that picked its own bin wins; otherwise fall back to
                # the rule's target, which must still clear the hard gate.
                loc = chosen[:1] if chosen else self._rule_candidates(rule, move_line)[:1]
                if not loc:
                    continue
                proposals.append(
                    {
                        "location_id": loc.id,
                        "score": score,
                        "rule_id": rule.id,
                        "reason": reason,
                        "tier": rule.tier,
                    }
                )
        proposals.sort(key=lambda d: (d.get("tier") or 9999, -d["score"]))
        return proposals

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    @api.model
    def _score_rule(self, rule, move_line):
        """Return ``(score:int 0..100, reason:str, location|None)``."""
        kind = rule.kind
        handler = getattr(self, f"_score_{kind}", None)
        if not handler:
            return 0, "", None
        try:
            result = handler(rule, move_line)
        except Exception as exc:  # pragma: no cover - defensive
            _logger.warning("Putaway scoring failed for rule=%s kind=%s: %s", rule.id, kind, exc)
            return 0, _("error: %s") % exc, None
        if not isinstance(result, tuple):
            return 0, "", None
        if len(result) == 2:
            score, reason = result
            return score, reason, None
        if len(result) == 3:
            return result
        return 0, "", None

    def _score_fixed_location(self, rule, move_line):
        loc = rule.target_location_id
        if not loc:
            return 0, "", None
        if not self._feasible_locations(loc, move_line):
            return 0, _("Fixed location rejected by capacity/reservation"), None
        return 100, _("Fixed location"), loc

    def _score_nearest_empty(self, rule, move_line):
        """Pick the *closest* empty bin, not merely the first one found."""
        cands = self._rule_candidates(rule, move_line)
        empties = self._empty_locations(cands)
        if not empties:
            return 0, "", None
        origin = rule._dock_location() or move_line.location_id
        best = min(empties, key=lambda loc: (loc._wms_walk_distance_to(origin), loc.complete_name or ""))
        distance = best._wms_walk_distance_to(origin)
        if distance == float("inf"):
            score = 70
            detail = _("no walk order configured")
        else:
            # 0 steps -> 95, decaying to a 70 floor at 25+ steps away.
            score = int(max(70, 95 - min(25.0, distance)))
            detail = _("%.0f steps from %s") % (distance, (origin.display_name if origin else "-"))
        return score, _("Nearest empty: %(loc)s (%(detail)s)") % {"loc": best.display_name, "detail": detail}, best

    def _score_zone_round_robin(self, rule, move_line):
        """Spread stock across a zone by rotating the chosen bin."""
        cands = self._rule_candidates(rule, move_line)
        if not cands:
            return 0, "", None
        # Rotate deterministically on the rule's own counter so consecutive
        # putaways land in different bins instead of stacking on the first.
        idx = rule._next_round_robin_index(len(cands))
        chosen = cands[idx]
        return 70, _("Zone round-robin -> %s") % chosen.display_name, chosen

    def _score_by_volume(self, rule, move_line):
        """Best volumetric fit, preferring the tightest bin that still fits.

        Capacity source, most authoritative first:
          1. ``stock.storage.category`` capacity for the handling unit / product
             (the native Odoo 19 model);
          2. the location's legacy ``volume_capacity_m3``.
        """
        cands = self._rule_candidates(rule, move_line)
        if not cands:
            return 0, _("Oversized: no feasible location"), None
        product = move_line.product_id
        qty = self._move_line_qty(move_line)

        best = None
        best_free = None
        for loc in cands:
            native_free = self._native_capacity_free(loc, product, move_line)
            if native_free is not None:
                if native_free < self._required_capacity(product, qty, move_line):
                    continue
                free = native_free
            else:
                capacity = loc.volume_capacity_m3 or 0.0
                if capacity <= 0.0:
                    continue
                used = loc.volume_used_m3 or 0.0
                needed = (product.volume or 0.0) * qty
                if used + needed > capacity:
                    continue
                free = capacity - used - needed
            if best is None or free < best_free:
                best, best_free = loc, free
        if best is None:
            return 0, _("Oversized: no bin with enough free capacity"), None
        # Tightest fit wins: less leftover space -> higher score.
        ratio = 0.0
        if best.volume_capacity_m3:
            ratio = max(0.0, min(1.0, best_free / best.volume_capacity_m3))
        score = int(60 + 40 * (1.0 - ratio))
        return score, _("Volume fit: %(free).2f free in %(loc)s") % {"free": best_free, "loc": best.display_name}, best

    @api.model
    def _native_capacity_free(self, location, product, move_line):
        """Remaining units allowed by ``stock.storage.category``, or None.

        Returns ``None`` when the location has no storage category or the
        category declares no capacity row for this product / package type —
        i.e. "the native model has no opinion here".
        """
        category = location.storage_category_id
        if not category:
            return None
        ptype = product._wms_package_type(move_line=move_line)
        capacity = category.capacity_ids.filtered(
            lambda c: (ptype and c.package_type_id == ptype) or (not ptype and c.product_id == product)
        )[:1]
        if not capacity:
            return None
        if ptype:
            packages = self.env["stock.package"].search_count(
                [("location_id", "=", location.id), ("package_type_id", "=", ptype.id)]
            )
            return max(0.0, (capacity.quantity or 0.0) - packages)
        on_hand = sum(
            self.env["stock.quant"]
            .search([("location_id", "=", location.id), ("product_id", "=", product.id)])
            .mapped("quantity")
        )
        return max(0.0, (capacity.quantity or 0.0) - on_hand)

    @api.model
    def _required_capacity(self, product, qty, move_line) -> float:
        """How many capacity units this move line consumes."""
        if product._wms_package_type(move_line=move_line):
            return product._wms_package_count(qty)
        return qty

    def _score_by_dimension(self, rule, move_line):
        """Slot by physical fit: PxLxT of the handling unit vs. the bin opening.

        Prefers the *snuggest* bin — the one whose leftover floor area is
        smallest — so large bins stay free for large goods.
        """
        product = move_line.product_id
        pkg_dims = product._wms_package_dims_mm(move_line=move_line)
        if not any(pkg_dims):
            return 0, _("No packaging dimensions on %s") % product.display_name, None
        cands = self._rule_candidates(rule, move_line)
        if not cands:
            return 0, _("No bin fits %.0fx%.0fx%.0f mm") % pkg_dims, None

        pl, pw, ph = pkg_dims
        pkg_floor = sorted((pl, pw))
        best = None
        best_waste = None
        for loc in cands:
            bl, bw, bh = loc._wms_dims_mm()
            if not (bl and bw):
                continue
            bin_floor = sorted((bl, bw))
            waste = (bin_floor[0] * bin_floor[1]) - (pkg_floor[0] * pkg_floor[1])
            if bh and ph:
                waste += (bh - ph) * bin_floor[0]
            if best is None or waste < best_waste:
                best, best_waste = loc, waste
        if best is None:
            # Geometry unknown on every candidate: fall back to the first
            # feasible bin rather than silently returning nothing.
            return 65, _("Dimension rule: bins have no geometry, first fit used"), cands[:1]
        bl, bw, bh = best._wms_dims_mm()
        bin_area = sorted((bl, bw))[0] * sorted((bl, bw))[1]
        tightness = 1.0 - min(1.0, max(0.0, best_waste / bin_area)) if bin_area else 0.0
        score = int(70 + 25 * tightness)
        return (
            score,
            _("Fits %(p)s in %(loc)s (%(t).0f%% filled)")
            % {"p": "%.0fx%.0fx%.0f mm" % pkg_dims, "loc": best.display_name, "t": tightness * 100},
            best,
        )

    def _score_by_weight(self, rule, move_line):
        """Slot by remaining weight headroom; heaviest-capacity bin wins."""
        product = move_line.product_id
        qty = self._move_line_qty(move_line)
        gross = product._wms_gross_weight_kg(qty, move_line=move_line)
        if gross <= 0.0:
            return 0, _("No weight on %s") % product.display_name, None
        cands = self._rule_candidates(rule, move_line)
        if not cands:
            return 0, _("No bin with %.2f kg free") % gross, None
        best = max(cands, key=lambda loc: loc._wms_free_weight_kg())
        free = best._wms_free_weight_kg()
        if free == float("inf"):
            return 70, _("Weight fit: %s has no ceiling") % best.display_name, best
        used_ratio = min(1.0, gross / free) if free else 1.0
        score = int(70 + 25 * (1.0 - used_ratio))
        return score, _("Weight fit: %(g).2f kg into %(f).2f kg free") % {"g": gross, "f": free}, best

    def _score_by_temperature(self, rule, move_line):
        if not rule.temperature_zone:
            return 0, "", None
        # No standard field; treat as match if rule's zone is set and target exists
        if not rule.target_location_id:
            return 0, "", None
        return 75, _("Temperature zone: %s") % rule.temperature_zone, rule.target_location_id

    def _score_by_abc_velocity(self, rule, move_line):
        product = move_line.product_id
        abc = getattr(product, "abc_class", "B") or "B"
        # A items near dock — proxy: any rule with abc_class='A' scores higher
        weight = {"A": 95, "B": 75, "C": 55}.get(abc, 50)
        if rule.abc_class and rule.abc_class != abc:
            return 0, "", None
        return weight, _("ABC velocity (%s)") % abc, None

    def _score_custom_python(self, rule, move_line):
        expr = (rule.custom_python or "").strip()
        if not expr:
            return 0, "", None
        product = move_line.product_id
        candidate_locations = rule._candidate_locations()
        eval_ctx = {
            "move_line": move_line,
            "product": product,
            "candidate_locations": candidate_locations,
            "env": self.env,
        }
        # safe_eval blocks attribute access to dangerous builtins by default.
        # Odoo 19 takes exactly (expr, context) positionally — the old
        # (expr, globals, locals, nocopy=...) shape raises TypeError.
        try:
            result = safe_eval(expr, eval_ctx, mode="eval")
        except Exception as exc:
            _logger.warning("custom_python rejected: %s", exc)
            return 0, _("custom_python rejected (unsafe): %s") % exc, None
        if not isinstance(result, tuple) or len(result) != 2:
            return 0, _("custom_python must return (location_id, score) tuple"), None
        loc_id, score = result
        try:
            score = int(score)
        except Exception:
            return 0, _("custom_python score must be int"), None
        chosen = self.env["stock.location"].browse(loc_id) if loc_id else None
        if chosen and not chosen.exists():
            chosen = None
        if chosen:
            chosen = self._feasible_locations(chosen, move_line) or None
        return max(0, min(100, score)), _("custom_python"), chosen

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------

    @api.model
    def apply_top_proposal(self, move_line):
        """Auto-apply the top proposal when its confidence clears the threshold."""
        proposals = self.propose(move_line)
        if not proposals:
            return False
        top = proposals[0]
        Suggestion = self.env["custom.wms.putaway.suggestion"]
        sugg = Suggestion.create(
            {
                "picking_id": move_line.picking_id.id,
                "move_line_id": move_line.id,
                "original_dest_location_id": move_line.location_dest_id.id,
                "suggested_location_id": top["location_id"],
                "rule_id": top["rule_id"],
                "score": top["score"],
                "reason": top["reason"],
            }
        )
        if top["score"] >= self._auto_apply_threshold() and top["location_id"]:
            sugg.action_apply()
        return sugg
