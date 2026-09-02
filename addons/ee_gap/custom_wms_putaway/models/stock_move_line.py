# -*- coding: utf-8 -*-
"""stock.move.line hooks — auto-propose putaway, enforce bin reservation.

Two deliberate design points:

* A putaway failure must never block goods receipt. The engine call stays
  wrapped, but the failure is now *surfaced* on the picking chatter instead of
  disappearing into the log — an operator who never learns the suggestion
  failed will happily walk goods to the wrong bin.
* Category reservation is advisory by default (the engine simply will not
  suggest a reserved bin). It becomes a hard block only on locations flagged
  ``wms_enforce_categ``, so turning it on is an explicit warehouse decision.
"""

from __future__ import annotations

import logging

from odoo import _, api, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    def _is_incoming(self) -> bool:
        self.ensure_one()
        ptype = self.picking_id.picking_type_id
        return bool(ptype and ptype.code == "incoming")

    # ------------------------------------------------------------------
    # Category reservation
    # ------------------------------------------------------------------

    @api.constrains("location_dest_id", "product_id")
    def _check_wms_location_reservation(self):
        for rec in self:
            dest = rec.location_dest_id
            if not dest or not dest.wms_enforce_categ:
                continue
            if dest._wms_accepts_category(rec.product_id.categ_id):
                continue
            raise ValidationError(
                _(
                    "Location %(loc)s is reserved for %(allowed)s. "
                    "%(product)s belongs to %(categ)s and cannot be stored there."
                )
                % {
                    "loc": dest.display_name,
                    "allowed": ", ".join(dest.wms_allowed_categ_ids.mapped("display_name")) or _("(none)"),
                    "product": rec.product_id.display_name,
                    "categ": rec.product_id.categ_id.display_name or _("(no category)"),
                }
            )

    # ------------------------------------------------------------------
    # Putaway auto-proposal
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        engine = self.env["custom.putaway.engine"]
        for rec in records:
            try:
                if rec._is_incoming():
                    engine.apply_top_proposal(rec)
            except Exception as exc:  # pragma: no cover - never block create
                _logger.warning("putaway auto-propose failed for ml=%s: %s", rec.id, exc)
                rec._notify_putaway_failure(exc)
        return records

    def _notify_putaway_failure(self, exc):
        """Make a silent engine failure visible to the receiving operator."""
        self.ensure_one()
        picking = self.picking_id
        if not picking:
            return
        try:
            picking.message_post(
                body=_(
                    "Putaway suggestion could not be generated for %(product)s: "
                    "%(error)s. Choose the destination bin manually."
                )
                % {"product": self.product_id.display_name, "error": exc},
                message_type="comment",
            )
        except Exception:  # pragma: no cover - chatter must never break receipt
            _logger.exception("Could not post putaway failure notice on picking %s", picking.id)
