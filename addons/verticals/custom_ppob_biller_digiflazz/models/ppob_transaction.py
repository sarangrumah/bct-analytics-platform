# -*- coding: utf-8 -*-
from odoo import fields, models


class PpobTransaction(models.Model):
    _inherit = "custom.ppob.transaction"

    digiflazz_ref_id = fields.Char(
        readonly=True,
        copy=False,
        index=True,
        help="The ref_id sent to Digiflazz. Digiflazz deduplicates by this "
        "value, which makes it the ONLY thing standing between a status "
        "check and a duplicate sale: a prepaid status check re-sends the "
        "topup, and an unrecognised ref_id books a new one.\n\n"
        "Derived once from the transaction name (slashes stripped, since "
        "the sequence produces PPOB/YYYYMMDD/NNNNNNNN) and never "
        "regenerated.\n\n"
        "copy=False is load-bearing, not tidiness: action_retry clones the "
        "transaction, and a clone inheriting the parent's ref_id would "
        "make Digiflazz replay the ORIGINAL outcome instead of selling "
        "again -- the retry would look like it worked while delivering "
        "nothing. A retry is a new sale by design.",
    )

    def _digiflazz_build_ref_id(self):
        """Stable, URL/JSON-safe ref_id derived from the transaction name.

        The name is globally unique (single ir.sequence, no company scoping),
        unlike idempotency_key which is only unique PER MITRA -- two mitra can
        legitimately submit the same key, and colliding on Digiflazz's side
        would make one mitra's sale answer the other's status check.
        """
        self.ensure_one()
        return (self.name or "").replace("/", "-")
