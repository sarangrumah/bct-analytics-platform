# -*- coding: utf-8 -*-
"""Persistent audit + idempotency log for DP 100% provider topup.

The wizard ``custom.ppob.provider.topup.wizard`` is a TransientModel and its
rows are garbage-collected periodically, which makes cross-session retry-safety
hard. This non-transient log keeps a permanent record of each topup request and
its DP / Pelunasan vendor-bill pair so a re-run with the same ``request_uid``
returns the existing pair instead of duplicating.
"""

import uuid

from odoo import api, fields, models


class PpobProviderTopupLog(models.Model):
    _name = "custom.ppob.provider.topup.log"
    _description = "PPOB Provider DP 100% Topup Log"
    _order = "create_date desc"
    _rec_name = "request_uid"

    provider_id = fields.Many2one(
        comodel_name="custom.ppob.provider",
        required=True,
        ondelete="restrict",
    )
    bucket_id = fields.Many2one(
        comodel_name="custom.ppob.provider.bucket",
        required=True,
        ondelete="restrict",
    )
    request_uid = fields.Char(
        required=True,
        copy=False,
        index=True,
        default=lambda self: "PRV-TOPUP-" + uuid.uuid4().hex[:12].upper(),
        help="Unique key for this topup request. Re-running the wizard with the "
        "same value is a no-op (returns existing pair).",
    )
    gross_amount = fields.Monetary(currency_field="currency_id", required=True)
    discount_amount = fields.Monetary(currency_field="currency_id", default=0.0)
    paid_amount = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_paid_amount",
        store=True,
        help="Net cash outflow = gross - discount.",
    )
    dpp_amount = fields.Monetary(currency_field="currency_id")
    ppn_amount = fields.Monetary(currency_field="currency_id")
    coretax_method = fields.Selection(
        selection=[
            ("dpp_nilai_lain", "DPP nilai lain"),
            ("gross_minus_ppn", "Gross - PPN"),
        ],
        readonly=True,
        help="Snapshot of provider.coretax_method at request time.",
    )
    timing = fields.Selection(
        selection=[
            ("dp_post", "Bucket credit at DP post"),
            ("pelunasan_post", "Bucket credit at Pelunasan post"),
        ],
        readonly=True,
        help="Snapshot of provider.topup_dp_timing at request time.",
    )
    dp_invoice_id = fields.Many2one(
        comodel_name="account.move",
        string="DP 100% Bill",
        ondelete="set null",
    )
    pelunasan_invoice_id = fields.Many2one(
        comodel_name="account.move",
        string="Pelunasan Bill",
        ondelete="set null",
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("posted", "Posted"),
            ("cancel", "Cancelled"),
        ],
        default="draft",
        required=True,
    )
    reason = fields.Char()
    currency_id = fields.Many2one(
        related="provider_id.currency_id",
        store=True,
        readonly=True,
    )

    _request_uid_uniq = models.Constraint(
        "unique(request_uid)",
        "Topup request_uid must be unique.",
    )

    @api.depends("gross_amount", "discount_amount")
    def _compute_paid_amount(self):
        for log in self:
            log.paid_amount = (log.gross_amount or 0.0) - (log.discount_amount or 0.0)
