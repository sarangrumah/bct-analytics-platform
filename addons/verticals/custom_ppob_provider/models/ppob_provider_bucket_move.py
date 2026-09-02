# -*- coding: utf-8 -*-
from odoo import fields, models


class PpobProviderBucketMove(models.Model):
    _name = "custom.ppob.provider.bucket.move"
    _description = "PPOB Provider Bucket Move"
    _order = "date desc, id desc"

    name = fields.Char(
        required=True,
        copy=False,
        default=lambda self: self.env["ir.sequence"].next_by_code("custom.ppob.provider.bucket.move") or "/",
    )
    bucket_id = fields.Many2one(
        comodel_name="custom.ppob.provider.bucket",
        required=True,
        ondelete="restrict",
        index=True,
    )
    provider_id = fields.Many2one(
        related="bucket_id.provider_id",
        store=True,
        readonly=True,
    )
    mode = fields.Selection(
        related="bucket_id.mode",
        store=True,
        readonly=True,
    )
    product_id = fields.Many2one(
        related="bucket_id.product_id",
        store=True,
        readonly=True,
    )
    date = fields.Datetime(default=fields.Datetime.now, required=True, index=True)
    type = fields.Selection(
        selection=[
            ("topup", "Topup"),
            ("usage", "Usage (Sale)"),
            ("refund", "Refund"),
            ("adjust", "Adjustment"),
        ],
        required=True,
    )
    amount_signed = fields.Monetary(currency_field="currency_id", required=True)
    tax_amount = fields.Monetary(
        currency_field="currency_id",
        default=0.0,
        help="Input VAT split out of the gross topup amount.",
    )
    gross_amount = fields.Monetary(
        currency_field="currency_id",
        default=0.0,
        help="Gross amount paid (DPP + PPN) for topup moves.",
    )
    balance_after = fields.Monetary(currency_field="currency_id", readonly=True)
    ref = fields.Char()
    move_id = fields.Many2one(
        comodel_name="account.move",
        string="Journal Entry",
        readonly=True,
        ondelete="restrict",
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("posted", "Posted"),
            ("reversed", "Reversed"),
        ],
        default="draft",
        required=True,
    )
    # ppob_transaction_id added by custom_ppob_sale to avoid cross-module dep.
    currency_id = fields.Many2one(
        related="bucket_id.currency_id",
        store=True,
        readonly=True,
    )
