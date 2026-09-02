# -*- coding: utf-8 -*-
"""POS configuration: rupiah rounding + e-receipt account binding."""

from odoo import fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    x_rupiah_rounding = fields.Selection(
        [
            ("none", "No rounding"),
            ("50", "Nearest IDR 50"),
            ("100", "Nearest IDR 100"),
            ("500", "Nearest IDR 500"),
            ("1000", "Nearest IDR 1,000"),
        ],
        string="Rupiah Rounding",
        default="100",
        help="Round cash kembalian to the nearest IDR.",
    )
    x_rupiah_rounding_strategy = fields.Selection(
        [
            ("up", "Round up (favor merchant)"),
            ("down", "Round down (favor customer)"),
            ("nearest", "Round to nearest"),
        ],
        string="Rounding Strategy",
        default="nearest",
    )
    x_eperformance_receipt_whatsapp = fields.Boolean(
        string="E-Receipt via WhatsApp",
        default=True,
        help="Allow sending POS e-receipts via WhatsApp (custom_whatsapp).",
    )
    x_ereceipt_sms = fields.Boolean(
        string="E-Receipt via SMS",
        default=False,
        help="Allow sending POS e-receipts via SMS (custom_sms_id).",
    )
    x_whatsapp_account_id = fields.Many2one(
        "whatsapp.account",
        string="WhatsApp Account",
        help="Account used to dispatch POS e-receipts via WhatsApp.",
    )
    x_sms_account_id = fields.Many2one(
        "custom.sms.account",
        string="SMS Account",
        help="Account used to dispatch POS e-receipts via SMS.",
    )
