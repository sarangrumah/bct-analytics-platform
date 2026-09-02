# -*- coding: utf-8 -*-
"""Keep the MDM feed from tripping over the category guard — or slipping past it.

The nightly master-data push writes ``categ_id`` onto templates that already
exist (``custom_retail_import/models/product_template.py``,
``_mdm_template_vals`` → ``_mdm_apply_extended``). Two bad outcomes to avoid:

* the guard raises inside a cron and the whole MDM batch dies;
* the feed is exempted and quietly re-categorises a product that has been sold,
  which is exactly what this module exists to prevent.

So the category is stripped out of the MDM write and a **draft**
``retail.categ.reclass`` is raised instead. The rest of the payload (cost, HS
code, dimensions, …) applies as usual, and the category change becomes a
reviewable request carrying its own correction figures.

The X101/X24 lazy-create paths are untouched — they ``create`` products rather
than ``write`` to them, so no existing balance is at stake.
"""

import logging

from odoo import _, models

_logger = logging.getLogger(__name__)


class RetailImportExecutorCategGuard(models.AbstractModel):
    _inherit = "retail.import.executor"

    def _mdm_template_vals(self, tmpl, mdm, write_cost, apply_active, has_hs_code):
        vals = super()._mdm_template_vals(tmpl, mdm, write_cost, apply_active, has_hs_code)
        categ_id = vals.get("categ_id")
        if not categ_id:
            return vals
        new_categ = self.env["product.category"].browse(categ_id)
        if not tmpl._retail_categ_guard_blocks(new_categ):
            return vals
        vals.pop("categ_id")
        self._retail_park_categ_change(tmpl, new_categ)
        return vals

    def _retail_park_categ_change(self, tmpl, new_categ):
        """Raise a draft reclassification for a category change MDM may not make."""
        Reclass = self.env["retail.categ.reclass"].sudo()
        company = tmpl.company_id or self.env.company
        pending = Reclass.search(
            [
                ("state", "in", ("draft", "computed", "to_approve")),
                ("new_categ_id", "=", new_categ.id),
                ("product_tmpl_ids", "in", tmpl.id),
            ],
            limit=1,
        )
        if pending:
            return pending
        reclass = Reclass.create(
            {
                "company_id": company.id,
                "product_tmpl_ids": [(6, 0, [tmpl.id])],
                "new_categ_id": new_categ.id,
            }
        )
        _logger.warning(
            "MDM wanted to move %s from %s to %s, but it already has transactions. "
            "Category left unchanged; %s raised for Finance to review.",
            tmpl.display_name,
            tmpl.categ_id.display_name,
            new_categ.display_name,
            reclass.name,
        )
        tmpl.message_post(
            body=_(
                "The master-data feed asked to move this product to <b>%(categ)s</b>. "
                "It already has transactions, so the category was left as it is and "
                "reclassification <b>%(name)s</b> was raised for Finance to review.",
                categ=new_categ.display_name,
                name=reclass.name,
            )
        )
        return reclass
