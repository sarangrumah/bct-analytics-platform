# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


SIGNER_STATES = [
    ("waiting", "Waiting"),
    ("opened", "Opened"),
    ("signed", "Signed"),
    ("declined", "Declined"),
]


class SignRequestSigner(models.Model):
    _name = "sign.request.signer"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Sign Request Signer"
    _order = "request_id, sequence"

    request_id = fields.Many2one("sign.request", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    partner_id = fields.Many2one("res.partner")
    name = fields.Char(required=True)
    email = fields.Char(required=True)
    role = fields.Char(help="Free-text role label, e.g. 'Vendor', 'Approver'.")
    access_token = fields.Char(readonly=True, copy=False, index=True)
    state = fields.Selection(SIGNER_STATES, default="waiting", required=True, tracking=True)
    opened_at = fields.Datetime(readonly=True)
    signed_at = fields.Datetime(readonly=True)
    signature_data = fields.Binary(string="Signature (image)", attachment=True)
    signature_text = fields.Char(help="Typed-name fallback when drawing isn't available")
    ip_address = fields.Char(readonly=True)
    user_agent = fields.Char(readonly=True)

    def mark_opened(self, ip: str | None = None, ua: str | None = None):
        for rec in self:
            if rec.state == "waiting":
                rec.write(
                    {
                        "state": "opened",
                        "opened_at": fields.Datetime.now(),
                        "ip_address": ip,
                        "user_agent": ua,
                    }
                )

    def submit_signature(self, signature_data: bytes | None = None, signature_text: str | None = None):
        for rec in self:
            if rec.state in ("signed", "declined"):
                raise UserError(_("Signer already responded."))
            if not signature_data and not signature_text:
                raise UserError(_("Provide either a drawn signature or a typed name."))
            rec.write(
                {
                    "state": "signed",
                    "signed_at": fields.Datetime.now(),
                    "signature_data": signature_data,
                    "signature_text": signature_text,
                }
            )
            rec.request_id._pdp_audit_write(
                "sign_signer_signed",
                rec.request_id.id,
                {"signer_id": rec.id, "email": rec.email},
            )
            rec.request_id._refresh_state()

    def decline(self, reason: str | None = None):
        for rec in self:
            rec.write({"state": "declined"})
            rec.request_id.message_post(
                body=_("Signer %(name)s declined: %(reason)s", name=rec.name, reason=reason or "—"),
            )
