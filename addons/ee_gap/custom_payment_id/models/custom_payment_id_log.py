# -*- coding: utf-8 -*-
"""Outbound call log for Indonesia payment gateway adapters."""

from __future__ import annotations

from odoo import fields, models


class CustomPaymentIdLog(models.Model):
    _name = "custom.payment.id.log"
    _inherit = ["mail.thread"]
    _description = "Indonesia Payment Gateway Call Log"
    _order = "create_date desc, id desc"

    provider_id = fields.Many2one(
        "payment.provider",
        string="Provider",
        required=True,
        ondelete="cascade",
        index=True,
    )
    transaction_id = fields.Many2one(
        "payment.transaction",
        string="Transaction",
        ondelete="set null",
        index=True,
    )
    request_payload = fields.Text(string="Request Payload")
    response_payload = fields.Text(string="Response Payload")
    state = fields.Selection(
        [
            ("queued", "Queued"),
            ("sent", "Sent"),
            ("ok", "Ok"),
            ("failed", "Failed"),
            ("timeout", "Timeout"),
        ],
        string="State",
        default="queued",
        required=True,
        tracking=True,
        index=True,
    )
    http_status = fields.Integer(string="HTTP Status")
    latency_ms = fields.Integer(string="Latency (ms)")
    attempt = fields.Integer(string="Attempt", default=1)
    error_message = fields.Text(string="Error Message")
