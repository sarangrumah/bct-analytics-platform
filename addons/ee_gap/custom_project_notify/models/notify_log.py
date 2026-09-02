# -*- coding: utf-8 -*-
"""Delivery log: one row per channel per recipient.

Separate from ``pdp.audit.log`` on purpose. That one answers "who changed what"; this one
answers "was the human actually told" -- and a silent delivery failure is exactly the kind
of thing an audit trail will not surface.
"""

from odoo import api, fields, models


class CustomProjectNotifyLog(models.Model):
    _name = "custom.project.notify.log"
    _description = "VAS Notification Delivery Log"
    _order = "create_date desc, id desc"

    outbox_id = fields.Many2one(
        "custom.project.notify.outbox",
        ondelete="set null",
        index=True,
    )
    event = fields.Char(required=True, index=True)
    res_model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)
    res_label = fields.Char(help="Human-readable name of the record at send time.")
    vertical_id = fields.Many2one("custom.project.vertical", index=True)

    channel = fields.Selection(
        [("wa", "WhatsApp"), ("email", "E-mail"), ("odoo", "Odoo inbox")],
        required=True,
        index=True,
    )
    transport = fields.Char(help="wahub / baileys / smtp / mail.thread")
    recipient_kind = fields.Char()
    recipient_name = fields.Char()
    recipient_email = fields.Char()
    recipient_phone_masked = fields.Char(
        help="Masked on purpose: a delivery log is read by many people, and this is PII.",
    )

    subject = fields.Char()
    body = fields.Text()
    success = fields.Boolean(index=True)
    skipped_reason = fields.Char(
        help="Set when a channel was not even attempted, e.g. the recipient has no number.",
    )
    error_message = fields.Char()
    attempt = fields.Integer(default=1)
    sent_at = fields.Datetime(default=fields.Datetime.now)

    @api.model
    def mask_phone(self, phone):
        if not phone:
            return ""
        digits = "".join(ch for ch in phone if ch.isdigit())
        if len(digits) <= 6:
            return "•" * len(digits)
        return f"{digits[:3]}{'•' * (len(digits) - 7)}{digits[-4:]}"
