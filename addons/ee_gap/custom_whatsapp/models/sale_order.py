# -*- coding: utf-8 -*-
"""Inherit sale.order to add a 'Send via WhatsApp' button."""

from __future__ import annotations

from odoo import _, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_whatsapp_send(self):
        """Open the WhatsApp send wizard pre-filled from this order."""
        self.ensure_one()
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

        body = _("Halo %(name)s,\n\nReferensi pesanan: %(order)s\nTotal: %(total)s") % {
            "name": partner.name or "",
            "order": self.name,
            "total": self.amount_total,
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
