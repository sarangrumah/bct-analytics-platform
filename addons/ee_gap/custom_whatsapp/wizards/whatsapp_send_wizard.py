# -*- coding: utf-8 -*-
"""TransientModel wizard: pick template + send a WhatsApp message."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class WhatsappSendWizard(models.TransientModel):
    _name = "whatsapp.send.wizard"
    _description = "Send WhatsApp Wizard"

    account_id = fields.Many2one(
        "whatsapp.account",
        string="Account",
        required=True,
        domain="[('is_active','=',True)]",
    )
    template_id = fields.Many2one(
        "whatsapp.template",
        string="Template",
        domain="[('account_id','=',account_id),('status','=','approved')]",
        help="Optional approved template to use. Leave empty to send a free-text message.",
    )
    partner_id = fields.Many2one("res.partner", string="Recipient")
    to_phone = fields.Char(string="To (Phone)", required=True)
    body = fields.Text(string="Message")

    # Source backref for audit (origin sale order / invoice / ticket)
    source_model = fields.Char(readonly=True)
    source_res_id = fields.Integer(readonly=True)

    @api.onchange("template_id")
    def _onchange_template_id(self):
        for w in self:
            if w.template_id and not w.body:
                w.body = w.template_id.body_text

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        for w in self:
            if w.partner_id and not w.to_phone:
                w.to_phone = w.partner_id._whatsapp_phone()

    def action_send(self):
        self.ensure_one()
        if not self.to_phone:
            raise UserError(_("Recipient phone is required."))

        Message = self.env["whatsapp.message"]
        msg = Message.create(
            {
                "account_id": self.account_id.id,
                "template_id": self.template_id.id if self.template_id else False,
                "to_partner_id": self.partner_id.id if self.partner_id else False,
                "to_phone": self.to_phone,
                "body": self.body or "",
                "direction": "outbound",
                "state": "draft",
            }
        )
        msg.action_send()

        return {
            "type": "ir.actions.act_window",
            "name": _("WhatsApp Message"),
            "res_model": "whatsapp.message",
            "view_mode": "form",
            "res_id": msg.id,
            "target": "current",
        }
