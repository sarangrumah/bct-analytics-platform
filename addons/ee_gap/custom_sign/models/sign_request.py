# -*- coding: utf-8 -*-
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError


STATES = [
    ("draft", "Draft"),
    ("sent", "Sent"),
    ("partially_signed", "Partially Signed"),
    ("signed", "Fully Signed"),
    ("cancelled", "Cancelled"),
]


class SignRequest(models.Model):
    _name = "sign.request"
    _description = "Sign Request"
    _inherit = ["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]
    _order = "create_date desc"

    name = fields.Char(default="New", readonly=True, copy=False)
    template_id = fields.Many2one("sign.template", required=True)
    attachment_id = fields.Many2one(related="template_id.attachment_id", readonly=True, store=True)
    signer_ids = fields.One2many("sign.request.signer", "request_id", string="Signers")
    state = fields.Selection(STATES, default="draft", required=True, tracking=True, index=True)
    sent_at = fields.Datetime(readonly=True)
    completed_at = fields.Datetime(readonly=True)
    requested_by_id = fields.Many2one("res.users", default=lambda s: s.env.user, required=True)
    notes = fields.Text()
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    signed_count = fields.Integer(compute="_compute_signed_count")
    total_signers = fields.Integer(compute="_compute_signed_count")

    def _pdp_audit_classification(self):
        return "financial"

    @api.depends("signer_ids.state")
    def _compute_signed_count(self):
        for rec in self:
            rec.total_signers = len(rec.signer_ids)
            rec.signed_count = len(rec.signer_ids.filtered(lambda s: s.state == "signed"))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("sign.request") or "SIGN-???"
        return super().create(vals_list)

    def action_send(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only draft requests can be sent."))
            if not rec.signer_ids:
                raise UserError(_("Add at least one signer."))
            for signer in rec.signer_ids:
                if not signer.access_token:
                    signer.access_token = secrets.token_urlsafe(32)
            rec.write({"state": "sent", "sent_at": fields.Datetime.now()})
            rec._pdp_audit_write("sign_request_sent", rec.id, {"signer_count": len(rec.signer_ids)})

    def action_cancel(self):
        for rec in self:
            if rec.state == "signed":
                raise UserError(_("Cannot cancel a fully-signed request."))
            rec.write({"state": "cancelled"})
            rec._pdp_audit_write("sign_request_cancel", rec.id, None)

    def _refresh_state(self):
        for rec in self:
            if not rec.signer_ids:
                continue
            all_signed = all(s.state == "signed" for s in rec.signer_ids)
            any_signed = any(s.state == "signed" for s in rec.signer_ids)
            if all_signed:
                rec.write({"state": "signed", "completed_at": fields.Datetime.now()})
                rec._pdp_audit_write("sign_request_complete", rec.id, None)
            elif any_signed and rec.state == "sent":
                rec.write({"state": "partially_signed"})
