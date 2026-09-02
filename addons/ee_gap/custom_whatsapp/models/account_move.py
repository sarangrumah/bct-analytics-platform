# -*- coding: utf-8 -*-
"""Inherit account.move to add a 'Send Invoice via WhatsApp' button."""

from __future__ import annotations

from odoo import _, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    def action_whatsapp_send(self):
        """Open the WhatsApp send wizard pre-filled from this invoice."""
        self.ensure_one()
        if self.move_type not in ("out_invoice", "out_refund"):
            raise UserError(_("Only customer invoices/refunds can be sent via WhatsApp."))

        partner = self.partner_id
        phone = partner._whatsapp_phone()
        if not phone:
            raise UserError(_("Customer %s has no phone/mobile set; cannot send WhatsApp.") % partner.display_name)

        Account = self.env["whatsapp.account"]
        account = Account.search(
            [("is_active", "=", True), ("company_id", "in", (False, self.company_id.id))],
            limit=1,
        )
        if not account:
            raise UserError(_("No active WhatsApp account configured."))

        body = _("Halo %(name)s,\n\nInvoice %(invoice)s sebesar %(total)s telah diterbitkan. Jatuh tempo: %(due)s.") % {
            "name": partner.name or "",
            "invoice": self.name or "",
            "total": self.amount_total,
            "due": self.invoice_date_due or "",
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
