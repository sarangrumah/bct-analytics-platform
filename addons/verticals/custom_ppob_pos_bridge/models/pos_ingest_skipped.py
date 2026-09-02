# -*- coding: utf-8 -*-
"""Replay/audit queue for POS feed events that could not be projected
(unmapped mitra/product, non-terminal status, missing correlation). Nothing is
dropped silently; ops complete the mapping then replay.
"""

import json

from odoo import _, fields, models


class PosIngestSkipped(models.Model):
    _name = "custom.ppob.pos.ingest.skipped"
    _description = "POS Bridge: Skipped Ingest Events"
    _order = "create_date desc, id desc"

    feed = fields.Selection(
        selection=[("pos", "POS"), ("h2h", "H2H")],
        required=True,
        index=True,
    )
    external_ref = fields.Char(
        string="Feed External Ref",
        required=True,
        index=True,
        help="pos_trx_ref + ':pos'|':h2h' -- the per-feed idempotency key.",
    )
    pos_trx_ref = fields.Char(index=True)
    mitra_ref = fields.Char()
    product_code = fields.Char()
    skip_reason = fields.Selection(
        selection=[
            ("mitra_not_mapped", "Mitra code not mapped to a partner"),
            ("product_not_mapped", "Product code not in catalog"),
            ("non_terminal_status", "Event status is not terminal"),
            ("bad_payload", "Missing/invalid required fields"),
            ("post_error", "Error while projecting to GL"),
            ("other", "Other"),
        ],
        required=True,
        index=True,
    )
    error_detail = fields.Char()
    raw_payload = fields.Text(help="JSON dump of the event, for replay.")
    replayed = fields.Boolean(default=False, index=True)
    replayed_at = fields.Datetime(readonly=True)
    pos_txn_id = fields.Many2one(
        comodel_name="custom.ppob.pos.txn",
        string="Resulting Join Row",
        readonly=True,
    )

    _external_ref_uniq = models.Constraint("unique(external_ref)", "A single feed event can only have one skip record.")

    def action_replay(self):
        """Re-feed the stored payload through the ingest projector. Idempotent:
        already-replayed rows are no-ops; a still-unmappable payload stays."""
        Txn = self.env["custom.ppob.pos.txn"]
        replayed = 0
        for rec in self:
            if rec.replayed:
                continue
            try:
                payload = json.loads(rec.raw_payload or "{}")
            except (TypeError, ValueError):
                continue
            join = Txn._ingest_event(rec.feed, payload, replay=True)
            if join:
                rec.write(
                    {
                        "replayed": True,
                        "replayed_at": fields.Datetime.now(),
                        "pos_txn_id": join.id,
                    }
                )
                replayed += 1
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Replay Skipped Events"),
                "message": _("%s event(s) replayed.") % replayed,
                "type": "success" if replayed else "warning",
            },
        }
