# -*- coding: utf-8 -*-
"""Refuse a product-category change that would silently move the ledger.

The category decides the product's income / discount / return / expense /
valuation accounts. Once the product has been sold, changing it makes every
future posting land somewhere else while everything already posted stays where
it was — and nothing in the GL can even be traced back to the product, because
POS closing entries carry no ``product_id``.

So the plain write is refused and the user is sent to **Product Category
Reclassification** (``retail.categ.reclass``), which recomputes the impact, books
the correction, and is itself gated behind Finance approval.

Deliberately narrow. The write only fails when all three hold:

1. ``categ_id`` is actually being changed;
2. the product has movement (a POS line, a done stock move, or a posted journal
   item naming it);
3. the account mapping really differs between the two categories.

Point 3 is what keeps this out of everyone's way: the X101 merchandising tree
re-parents products inside the same COA bucket all the time, and none of that
touches the ledger.
"""

from odoo import _, models
from odoo.exceptions import UserError

# Set by the sanctioned path (an approved retail.categ.reclass) — a named
# constant rather than a bare True, following custom_bank_import's
# BYPASS_LOCK_CHECK.
BYPASS_CATEG_GUARD = "retail_categ_change_approved"


class ProductTemplate(models.Model):
    _inherit = "product.template"

    def _retail_has_movement(self):
        """True when anything in the ledger or the stock history names this product."""
        self.ensure_one()
        variants = self.product_variant_ids
        if not variants:
            return False
        if self.env["pos.order.line"].sudo().search_count([("product_id", "in", variants.ids)], limit=1):
            return True
        if (
            self.env["stock.move"]
            .sudo()
            .search_count([("product_id", "in", variants.ids), ("state", "=", "done")], limit=1)
        ):
            return True
        return bool(
            self.env["account.move.line"]
            .sudo()
            .search_count([("product_id", "in", variants.ids), ("parent_state", "=", "posted")], limit=1)
        )

    def _retail_categ_moves_the_gl(self, new_categ):
        """True when moving this product to ``new_categ`` changes an account.

        Resolution is delegated to ``retail.categ.reclass._retail_categ_accounts``
        so the guard and the correction it points at can never disagree about
        which accounts a category implies.
        """
        self.ensure_one()
        company = self.company_id or self.env.company
        Reclass = self.env["retail.categ.reclass"].sudo()
        variant = self.product_variant_ids[:1]
        if not variant:
            return False
        before = Reclass._retail_categ_accounts(company, variant, self.categ_id)
        after = Reclass._retail_categ_accounts(company, variant, new_categ)
        return any(before[kind] != after[kind] for kind in before)

    def _retail_categ_guard_blocks(self, new_categ):
        self.ensure_one()
        if not new_categ or new_categ == self.categ_id:
            return False
        return self._retail_has_movement() and self._retail_categ_moves_the_gl(new_categ)

    def write(self, vals):
        if "categ_id" in vals and vals["categ_id"] and not self.env.context.get(BYPASS_CATEG_GUARD):
            new_categ = self.env["product.category"].browse(vals["categ_id"])
            blocked = self.filtered(lambda tmpl: tmpl._retail_categ_guard_blocks(new_categ))
            if blocked:
                raise UserError(
                    _(
                        "%(products)s already have transactions, and moving them to "
                        "%(categ)s changes the accounts their revenue posts to.\n\n"
                        "Changing the category here would leave everything already "
                        "posted on the old accounts. Use Accounting → Journal Entries "
                        "→ Product Category Reclassification instead: it shows the "
                        "impact per day and per store, books the correction, and "
                        "routes it to Finance for approval.",
                        products=", ".join(blocked.mapped("display_name")[:5]) + ("…" if len(blocked) > 5 else ""),
                        categ=new_categ.display_name,
                    )
                )
        return super().write(vals)

    def action_retail_request_categ_change(self):
        """Open a reclassification pre-filled with the products in ``self``."""
        return {
            "type": "ir.actions.act_window",
            "name": _("Request Category Change"),
            "res_model": "retail.categ.reclass",
            "view_mode": "form",
            "target": "current",
            "context": {"default_product_tmpl_ids": [(6, 0, self.ids)]},
        }
