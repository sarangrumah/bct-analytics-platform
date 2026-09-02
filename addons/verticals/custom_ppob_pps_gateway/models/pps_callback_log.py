# -*- coding: utf-8 -*-
"""Audit log of outbound PPS callbacks (Odoo -> POS). One row per attempt."""

from odoo import fields, models


class PpsCallbackLog(models.Model):
    _name = "custom.ppob.pps.callback.log"
    _description = "PPS Gateway: Outbound Callback Log"
    _order = "create_date desc, id desc"

    transaction_id = fields.Many2one(
        comodel_name="custom.ppob.transaction",
        string="Transaction",
        required=True,
        ondelete="cascade",
        index=True,
    )
    url = fields.Char()
    querystring = fields.Text()
    http_status = fields.Integer()
    attempt_no = fields.Integer(default=1)
    ok = fields.Boolean(default=False, index=True)
    error = fields.Char()
