# -*- coding: utf-8 -*-
import logging
import secrets

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class EventRegistration(models.Model):
    _inherit = "event.registration"

    x_whatsapp_ticket_sent = fields.Boolean(
        string="WhatsApp Ticket Sent",
        default=False,
        tracking=True,
    )
    x_qr_token = fields.Char(
        string="QR Token",
        readonly=True,
        copy=False,
        index=True,
        default=lambda self: "",
    )
    x_checked_in_at = fields.Datetime(
        string="Checked-in At",
        readonly=True,
    )
    x_checked_in_by_user_id = fields.Many2one(
        "res.users",
        string="Checked-in By",
        readonly=True,
    )

    # ---------- create override: QR token ----------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("x_qr_token"):
                vals["x_qr_token"] = secrets.token_urlsafe(16)
        return super().create(vals_list)

    # ---------- WhatsApp ticket delivery ----------

    def action_send_whatsapp_ticket(self):
        """Send the registration ticket via WhatsApp using the event's template."""
        Message = self.env["whatsapp.message"]
        for reg in self:
            event = reg.event_id
            template = event.x_whatsapp_ticket_template_id
            if not template:
                _logger.info("Skip WA ticket: event %s has no whatsapp template", event.name)
                continue
            partner = reg.partner_id
            phone = partner and (partner.mobile or partner.phone) or False
            if not phone:
                _logger.info("Skip WA ticket reg %s: partner has no phone", reg.id)
                continue
            msg = Message.create(
                {
                    "template_id": template.id,
                    "partner_id": partner.id,
                    "phone": phone,
                    "body": template.body or "",
                    "model": reg._name,
                    "res_id": reg.id,
                }
            )
            try:
                msg.action_send()
            except Exception as e:  # pragma: no cover
                _logger.warning("WA ticket send failed reg=%s: %s", reg.id, e)
                continue
            reg.x_whatsapp_ticket_sent = True
            _logger.info(
                "WA ticket sent reg=%s event=%s phone=%s",
                reg.id,
                event.name,
                phone,
            )
        return True

    # ---------- QR check-in ----------

    @api.model
    def action_qr_checkin(self, qr_token):
        """Check-in a registration by QR token.

        Returns a JSON-serializable dict suitable for an HTTP route consumer.
        """
        if not qr_token:
            return {"ok": False, "error": "missing_token"}
        reg = self.search([("x_qr_token", "=", qr_token)], limit=1)
        if not reg:
            return {"ok": False, "error": "not_found"}
        if not reg.event_id.x_qr_checkin_enabled:
            return {
                "ok": False,
                "error": "qr_disabled",
                "registration_id": reg.id,
            }
        if reg.x_checked_in_at:
            return {
                "ok": True,
                "already": True,
                "registration_id": reg.id,
                "attendee": reg.partner_id.display_name or reg.name,
                "event": reg.event_id.name,
                "checked_in_at": fields.Datetime.to_string(reg.x_checked_in_at),
            }
        now = fields.Datetime.now()
        reg.write(
            {
                "x_checked_in_at": now,
                "x_checked_in_by_user_id": self.env.user.id,
            }
        )
        # also flip CE event.registration state to attended where available
        if "state" in reg._fields:
            try:
                reg.write({"state": "done"})
            except UserError:
                pass
        return {
            "ok": True,
            "already": False,
            "registration_id": reg.id,
            "attendee": reg.partner_id.display_name or reg.name,
            "event": reg.event_id.name,
            "checked_in_at": fields.Datetime.to_string(now),
        }

    def action_manual_checkin(self):
        """Button helper for manual check-in from the form view."""
        for reg in self:
            if reg.x_qr_token:
                self.action_qr_checkin(reg.x_qr_token)
        return True
