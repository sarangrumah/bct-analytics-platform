# -*- coding: utf-8 -*-
"""custom.putaway.engine — the SAP two-dimensional storage search.

The dispatcher in ``custom_wms_putaway`` resolves handlers by name
(``getattr(self, f"_score_{kind}")``), so inheriting the AbstractModel and
adding ``_score_sap_storage_search`` is enough to register the new kind.

Algorithm
---------
Given a product's storage type and section, walk the type's search sequence in
the outer loop and the section's in the inner loop. The first (type, section)
bucket holding a bin with enough free volume wins. Score decays with how far
down each sequence the search had to go::

    score = 100 - type_penalty * i - section_penalty * j

Two properties of this shape matter operationally:

* the score depends only on the search position, never on which bin inside a
  bucket was picked, so the number an operator sees on the handheld means
  exactly "how far from ideal is this slot";
* with the shipped penalties (12 / 1) every slot inside the *correct* storage
  type scores at least 91 and auto-applies, while the first type fallback lands
  at 87 and stops for review. That boundary is the point of the default.

Empty buckets are skipped without cost. This is not a detail: in the reference
layout the general section ``GA2`` exists only on the half-height and floor
levels, so ``FO1 x GA2`` has no bins at all. Charging a penalty for stepping
over a bucket that cannot exist would distort every score after it.
"""

from __future__ import annotations

from odoo import _, api, models


class PutawayEngine(models.AbstractModel):
    _inherit = "custom.putaway.engine"

    # ------------------------------------------------------------------
    # Volume bookkeeping (cm3)
    # ------------------------------------------------------------------

    @api.model
    def _sap_product_volume_ccm(self, product) -> float:
        """Volume of one unit of ``product`` in cm3, 0.0 when unknown."""
        if not product:
            return 0.0
        return product._sap_volume_ccm()

    @api.model
    def _sap_required_volume_ccm(self, product, qty: float) -> float:
        """Volume this move line needs in cm3."""
        return self._sap_product_volume_ccm(product) * max(qty or 0.0, 0.0)

    @api.model
    def _sap_used_volume_map(self, locations) -> dict:
        """cm3 in use per bin, in one query.

        Grouping by product as well as location is what makes this a single
        round trip: the volume of a bin's contents is the sum over its distinct
        products, and ``_read_group`` already returns exactly that shape.
        """
        if not locations:
            return {}
        groups = self.env["stock.quant"]._read_group(
            [("location_id", "in", locations.ids)],
            groupby=["location_id", "product_id"],
            aggregates=["quantity:sum"],
        )
        used: dict[int, float] = {}
        for location, product, total in groups:
            quantity = total or 0.0
            if quantity <= 0.0:
                continue
            used[location.id] = used.get(location.id, 0.0) + self._sap_product_volume_ccm(product) * quantity
        return used

    @api.model
    def _sap_locations_holding(self, locations, product) -> set:
        """Ids of bins already holding ``product``, in one query."""
        if not locations or not product:
            return set()
        groups = self.env["stock.quant"]._read_group(
            [("location_id", "in", locations.ids), ("product_id", "=", product.id)],
            groupby=["location_id"],
            aggregates=["quantity:sum"],
        )
        return {location.id for location, total in groups if (total or 0.0) > 0.0}

    # ------------------------------------------------------------------
    # Bucketing and bin choice
    # ------------------------------------------------------------------

    @api.model
    def _sap_bin_index(self, locations) -> dict:
        """Map ``(storage_type_id, storage_section_id) -> bins``.

        Bins missing either classification are dropped here. That is the only
        exclusion mechanism the search needs: a damage or stock-count location
        simply carries no storage type and can never be reached.
        """
        buckets: dict[tuple, list] = {}
        for location in locations:
            type_id = location.wms_storage_type_id.id
            section_id = location.wms_storage_section_id.id
            if not type_id or not section_id:
                continue
            buckets.setdefault((type_id, section_id), []).append(location.id)
        return {key: locations.browse(ids) for key, ids in buckets.items()}

    @api.model
    def _sap_pick_best_bin(self, bins, product, needed_ccm, used_map, holding, move_line, consolidate=True):
        """Best bin inside one bucket, or ``None`` when the bucket is full.

        Native ``stock.storage.category`` capacity stays authoritative wherever
        it has an opinion; the cm3 test is additive, because the native model
        counts units and packages but never volume.

        Ordering, in decreasing importance: a bin already holding the SKU
        (consolidation beats a marginally tighter fit -- splitting a SKU across
        bins costs far more picking time than a little wasted space), then the
        tightest remaining fit, then walk order, then name for determinism.
        """
        if not bins:
            return None
        qty = self._move_line_qty(move_line)
        required_units = self._required_capacity(product, qty, move_line)

        candidates = []
        for location in bins:
            native_free = self._native_capacity_free(location, product, move_line)
            if native_free is not None and native_free < required_units:
                continue
            capacity = location._sap_capacity_ccm()
            if capacity > 0.0:
                free = capacity - used_map.get(location.id, 0.0)
                if needed_ccm > free:
                    continue
            else:
                free = float("inf")
            candidates.append((location, free))
        if not candidates:
            return None

        def sort_key(item):
            location, free = item
            already_here = 0 if (consolidate and location.id in holding) else 1
            leftover = free - needed_ccm
            return (already_here, leftover, location.wms_walk_sequence or 0, location.complete_name or "")

        candidates.sort(key=sort_key)
        return candidates[0][0]

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    @api.model
    def _sap_score(self, rule, type_index: int, section_index: int) -> int:
        """Confidence for a hit at position ``(type_index, section_index)``."""
        raw = 100 - (rule.sap_type_penalty or 0) * type_index - (rule.sap_section_penalty or 0) * section_index
        return max(1, min(100, int(raw)))

    @api.model
    def _sap_product_type(self, product, rule):
        return product.wms_storage_type_id or rule.sap_default_type_id

    @api.model
    def _sap_product_section(self, product, rule):
        return product.wms_storage_section_id or rule.sap_default_section_id

    def _score_sap_storage_search(self, rule, move_line):
        """Return ``(score, reason, location|None)`` for the SAP 2D search."""
        product = move_line.product_id
        storage_type = self._sap_product_type(product, rule)
        storage_section = self._sap_product_section(product, rule)
        if not storage_type:
            return 0, _("No storage type on %s") % product.display_name, None
        if not storage_section:
            return 0, _("No storage section on %s") % product.display_name, None

        candidates = self._rule_candidates(rule, move_line)
        if not candidates:
            return 0, _("No feasible bin in the rule's scope"), None

        type_sequence = storage_type._search_sequence()
        section_sequence = storage_section._search_sequence()
        index = self._sap_bin_index(candidates)
        if not index:
            return 0, _("No bin in scope carries a storage type and section"), None

        qty = self._move_line_qty(move_line)
        needed_ccm = self._sap_required_volume_ccm(product, qty)
        used_map = self._sap_used_volume_map(candidates)
        holding = self._sap_locations_holding(candidates, product) if rule.sap_consolidate else set()

        for type_index, section_index, stype, ssection in self._sap_walk(rule, type_sequence, section_sequence):
            bucket = index.get((stype.id, ssection.id))
            if not bucket:
                # Bucket does not exist in this layout -- step over it without
                # charging a penalty (see module docstring).
                continue
            best = self._sap_pick_best_bin(
                bucket,
                product,
                needed_ccm,
                used_map,
                holding,
                move_line,
                consolidate=rule.sap_consolidate,
            )
            if not best:
                continue
            score = self._sap_score(rule, type_index, section_index)
            reason = _("SAP search %(type)s/%(section)s -> %(bin)s (type step %(i)s, section step %(j)s)") % {
                "type": stype.code,
                "section": ssection.code,
                "bin": best.display_name,
                "i": type_index,
                "j": section_index,
            }
            return score, reason, best

        exhausted = _("SAP search exhausted for %(type)s/%(section)s") % {
            "type": storage_type.code,
            "section": storage_section.code,
        }
        if rule.sap_fail_action == "overflow" and rule.sap_overflow_location_id:
            overflow = self._feasible_locations(rule.sap_overflow_location_id, move_line)
            if overflow:
                return 40, _("%s -> overflow") % exhausted, overflow[:1]
        return 0, exhausted, None

    @api.model
    def _sap_walk(self, rule, type_sequence, section_sequence):
        """Yield ``(type_index, section_index, type, section)`` in search order.

        The indices are always the position within the *product's own* sequence,
        independent of which loop is outer, so the score means the same thing
        under either search order.
        """
        if rule.sap_search_order == "section_first":
            for section_index, section in enumerate(section_sequence):
                for type_index, stype in enumerate(type_sequence):
                    yield type_index, section_index, stype, section
        else:
            for type_index, stype in enumerate(type_sequence):
                for section_index, section in enumerate(section_sequence):
                    yield type_index, section_index, stype, section
