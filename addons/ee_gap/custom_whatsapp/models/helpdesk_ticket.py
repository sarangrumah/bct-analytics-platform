# -*- coding: utf-8 -*-
"""Conditional inherit of helpdesk.ticket — only registered if the model exists.

``custom_helpdesk`` is NOT a hard dependency (the manifest does not
list it) so this module remains installable on tenants without the
helpdesk app. The inherit registers gracefully — if helpdesk is absent,
Odoo simply ignores the model since the parent isn't loaded.
"""

from __future__ import annotations

from odoo import _, models
from odoo.exceptions import UserError


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    def action_whatsapp_notify(self):
        """Open the WhatsApp wizard pre-filled from this ticket."""
        self.ensure_one()
        partner = self.partner_id
        if not partner:
            raise UserError(_("Ticket has no customer set."))
        phone = partner._whatsapp_phone()
        if not phone:
            raise UserError(_("Customer %s has no phone/mobile set; cannot send WhatsApp.") % partner.display_name)

        Account = self.env["whatsapp.account"]
        account = Account.search(
            [("is_active", "=", True)],
            limit=1,
        )
        if not account:
            raise UserError(_("No active WhatsApp account configured."))

        body = _("Halo %(name)s,\n\nUpdate tiket %(ticket)s: %(subject)s\nStatus: %(state)s") % {
            "name": partner.name or "",
            "ticket": self.name or "",
            "subject": getattr(self, "subject", "") or self.name or "",
            "state": self.state or "",
        }

        return {
            "type": "ir.actions.act_window",
            "name": _("Send WhatsApp"),
            "res_model": "whatsapp.send.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_account_id": account.id,
                "default_partner_id": partner.id,
                "default_to_phone": phone,
                "default_body": body,
                "default_source_model": self._name,
                "default_source_res_id": self.id,
            },
        }
