# -*- coding: utf-8 -*-
"""Crosswalk from the MDM feed's taxonomy to Odoo product categories.

This exists because the two taxonomies do not agree. The X101 material master
categorises with a gender-prefixed three-level tree -- CATEGORY (``MENS BOTTOMS``),
CLASS (``JEANS``), SUBCLASS (``SLIM``) -- while the MDM payload sends a two-level
``category1``/``category2`` pair (``BOTTOMS``/``LONG BOTTOMS``) plus gender in
``udf8``. Level 1 reconstructs cleanly, but ``LONG BOTTOMS`` is not a CLASS value
and there is no SUBCLASS source at all.

That is not cosmetic: ``product.categ_id`` drives the revenue and COGS accounts. A
naive mapping would grow a second ``BOTTOMS`` root next to ``MENS BOTTOMS`` and split
the GL. So the mapping is data, reviewable and signed off, and anything it does not
cover still creates the product -- sales must be able to post -- but flags it
``mdm_category_unmapped`` so the wrong-taxonomy risk is visible instead of silent.
"""

from __future__ import annotations

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

#: MDM sends the singular; X101's category names use the possessive plural.
GENDER_PREFIX = {
    "MEN": "MENS",
    "MENS": "MENS",
    "MAN": "MENS",
    "WOMEN": "WOMENS",
    "WOMENS": "WOMENS",
    "WOMAN": "WOMENS",
}


class RetailMdmCategoryMap(models.Model):
    _name = "retail.mdm.category.map"
    _description = "MDM Category Crosswalk"
    _order = "category1, category2, gender"

    name = fields.Char(compute="_compute_name", store=True)
    gender = fields.Char(index=True, help="udf8 as sent (MEN/WOMEN). Blank matches any gender.")
    category1 = fields.Char(required=True, index=True)
    category2 = fields.Char(index=True, help="Blank matches any category2.")

    categ_id = fields.Many2one(
        "product.category",
        string="Product Category",
        ondelete="restrict",
        help="Pin directly to an existing category. Wins over the X101 triple below.",
    )
    x101_category = fields.Char(string="X101 CATEGORY", help="e.g. MENS BOTTOMS")
    x101_class = fields.Char(string="X101 CLASS", help="e.g. JEANS")
    x101_subclass = fields.Char(string="X101 SUBCLASS", help="e.g. SLIM")

    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company, required=True, index=True)

    # Odoo 19 silently ignores _sql_constraints.
    _map_uniq = models.Constraint(
        "unique(company_id, gender, category1, category2)",
        "A crosswalk entry for this gender/category1/category2 combination already exists.",
    )

    @api.depends("gender", "category1", "category2")
    def _compute_name(self):
        for rec in self:
            parts = [p for p in (rec.gender, rec.category1, rec.category2) if p]
            rec.name = " / ".join(parts) or "-"

    # ------------------------------------------------------------------
    @api.model
    def _lookup(self, gender, category1, category2):
        """Most-specific match first: (g, c1, c2) -> (g, c1, '') -> ('', c1, '')."""
        if not category1:
            return self.browse()
        gender = (gender or "").strip().upper()
        category1 = (category1 or "").strip().upper()
        category2 = (category2 or "").strip().upper()
        base = [("company_id", "=", self.env.company.id), ("category1", "=ilike", category1)]
        for domain in (
            base + [("gender", "=ilike", gender), ("category2", "=ilike", category2)],
            base + [("gender", "=ilike", gender), ("category2", "in", (False, ""))],
            base + [("gender", "in", (False, "")), ("category2", "in", (False, ""))],
        ):
            hit = self.search(domain, limit=1)
            if hit:
                return hit
        return self.browse()

    @api.model
    def resolve(self, gender, category1, category2, namespace="retail"):
        """Resolve an MDM category pair to (category_triple, mapped).

        Returns ``((cat, cls, subcls), mapped)`` where the triple feeds the existing
        X101 three-level category builder in ``_x101_upsert_items`` -- so a mapped and
        an unmapped product both end up under the same ``cat_l1_/l2_/l3_`` external IDs
        the file import uses, and no parallel tree appears.

        ``mapped`` is False when no crosswalk entry matched; the caller then sets
        ``mdm_category_unmapped`` on the template and flags the item needs_review.
        """
        hit = self._lookup(gender, category1, category2)
        if hit:
            if hit.categ_id:
                # A direct pin bypasses the triple entirely; the caller detects this
                # by the sentinel and writes categ_id straight onto the template.
                return (("__pinned__", str(hit.categ_id.id), ""), True)
            if hit.x101_category:
                return ((hit.x101_category, hit.x101_class or "", hit.x101_subclass or ""), True)

        # No entry: derive. Level 1 is the gender-prefixed form X101 uses; level 2 and
        # 3 both take category2, mirroring the file's own class==subclass rows
        # (SWEATERS/SWEATERS, SKIRTS/SKIRTS).
        prefix = GENDER_PREFIX.get((gender or "").strip().upper(), "")
        c1 = (category1 or "").strip().upper()
        c2 = (category2 or "").strip().upper()
        level1 = f"{prefix} {c1}".strip() if c1 else ""
        return ((level1, c2, c2), False)
