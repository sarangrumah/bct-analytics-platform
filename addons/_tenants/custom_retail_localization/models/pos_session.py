# -*- coding: utf-8 -*-
"""Operating-Unit stamping on the POS session closing entry (feature #9).

A POS session belongs to exactly one store (``config_id.warehouse_id``), so every
line of its closing entry belongs to that store's Operating Unit. Core builds
those lines without any analytic distribution, which left the whole POS stream
outside the per-OU reporting.

Every line core produces is stamped: the sale lines (Gross Sales), the tax lines
(VAT Out) and the receivable lines (per-tender receivable, or the POS Suspense
Clearing account in the retail-import decouple flow). Finance reads the OU
dimension off the whole entry, not just its P&L half, so a VAT or suspense line
without a distribution shows up as an unallocated hole when the entry is sliced
per store.

The distribution is merged through the same helper the purchase side uses, so the
OU is appended to its own analytic plan rather than replacing any distribution
core may have derived from the product.
"""

from odoo import models


class PosSession(models.Model):
    _inherit = "pos.session"

    def _retail_ou_analytic(self):
        """Operating-Unit analytic of this session's store, if any."""
        self.ensure_one()
        return self.config_id.warehouse_id.l10n_ou_analytic_id

    def _retail_stamp_ou(self, vals):
        """Merge this session's Operating Unit into one closing-entry line's vals."""
        ou = self._retail_ou_analytic()
        if not ou or not vals:
            return vals
        vals["l10n_ou_analytic_id"] = ou.id
        # Set the distribution explicitly rather than leaning on the
        # ``l10n_ou_analytic_id`` trigger: ``analytic_distribution`` is a stored
        # computed field with readonly=False, so a value supplied at create time
        # wins and the result stays deterministic. It also reaches lines whose
        # ``display_type`` ('tax', 'payment_term') the m2o trigger skips.
        vals["analytic_distribution"] = self.env["purchase.order.line"]._retail_merge_ou_distribution(
            vals.get("analytic_distribution"), ou.id
        )
        return vals

    def _get_sale_vals(self, key, sale_vals):
        return self._retail_stamp_ou(super()._get_sale_vals(key, sale_vals))

    def _get_tax_vals(self, key, amount, amount_converted, base_amount_converted):
        return self._retail_stamp_ou(super()._get_tax_vals(key, amount, amount_converted, base_amount_converted))

    def _get_combine_receivable_vals(self, payment_method, amount, amount_converted):
        return self._retail_stamp_ou(super()._get_combine_receivable_vals(payment_method, amount, amount_converted))

    def _get_split_receivable_vals(self, payment, amount, amount_converted):
        return self._retail_stamp_ou(super()._get_split_receivable_vals(payment, amount, amount_converted))
