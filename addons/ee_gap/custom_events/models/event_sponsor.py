# -*- coding: utf-8 -*-
from odoo import fields, models


class CustomEventSponsor(models.Model):
    _name = "custom.event.sponsor"
    _description = "Custom Event Sponsor"
    _order = "tier, sequence, name"

    name = fields.Char(string="Sponsor Name", required=True)
    logo = fields.Binary(string="Logo", attachment=True)
    tier = fields.Selection(
        [
            ("platinum", "Platinum"),
            ("gold", "Gold"),
            ("silver", "Silver"),
            ("bronze", "Bronze"),
        ],
        string="Tier",
        required=True,
        default="bronze",
        index=True,
    )
    event_id = fields.Many2one(
        "event.event",
        string="Event",
        required=True,
        ondelete="cascade",
        index=True,
    )
    currency_id = fields.Many2one(
        related="event_id.company_id.currency_id",
        string="Currency",
        readonly=True,
        store=True,
    )
    amount_paid = fields.Monetary(
        string="Amount Paid",
        currency_field="currency_id",
        default=0.0,
    )
    benefits = fields.Text(
        string="Benefits",
        help="Free-form description of sponsor benefits (logo placement, booth, etc.)",
    )
    sequence = fields.Integer(string="Sequence", default=10)
    active = fields.Boolean(default=True)
    website_url = fields.Char(string="Website URL")
