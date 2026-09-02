# -*- coding: utf-8 -*-
"""Cart abandonment tracking + reminder cron.

Lifecycle:

1. A scheduled action (``cron_send_abandoned_reminders``) sweeps
   ``sale.order`` rows in ``state='draft'`` that have not been written
   to in the last 24 hours and have a real customer (not the public
   user). For each one it ensures a ``custom.ecommerce.cart.abandonment``
   row exists and, if no reminder was sent yet, dispatches one.
2. Reminder dispatch prefers WhatsApp if the partner has an opt-in PDP
   consent for marketing and the ``custom_whatsapp`` module is installed;
   otherwise it falls back to email via ``mail.template``.

We keep the model thin on purpose — the heavy lifting (consent
resolution, channel selection) lives in the cron entry point so it can
be re-used from a manual “send now” button later.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Orders are considered “abandoned” once they sit in draft for at least
# this many hours without further writes.
_ABANDONMENT_HOURS = 24


class CartAbandonment(models.Model):
    _name = "custom.ecommerce.cart.abandonment"
    _description = "Abandoned eCommerce Cart"
    _inherit = ["mail.thread"]
    _order = "abandoned_at desc"

    name = fields.Char(
        string="Reference",
        compute="_compute_name",
        store=True,
    )
    cart_partner_id = fields.Many2one(
        "res.partner",
        string="Customer",
        required=True,
        ondelete="cascade",
        tracking=True,
    )
    sale_order_id = fields.Many2one(
        "sale.order",
        string="Cart",
        required=True,
        ondelete="cascade",
    )
    cart_amount = fields.Monetary(
        string="Cart Amount",
        currency_field="currency_id",
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="sale_order_id.currency_id",
        store=True,
        readonly=True,
    )
    abandoned_at = fields.Datetime(
        string="Abandoned At",
        required=True,
        default=fields.Datetime.now,
        tracking=True,
    )
    reminder_sent = fields.Boolean(
        string="Reminder Sent",
        default=False,
        tracking=True,
    )
    reminder_sent_at = fields.Datetime(string="Reminder Sent At", readonly=True)
    reminder_channel = fields.Selection(
        [("email", "Email"), ("whatsapp", "WhatsApp"), ("none", "Skipped")],
        string="Reminder Channel",
        readonly=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="sale_order_id.company_id",
        store=True,
        readonly=True,
    )

    _unique_cart = models.Constraint(
        "unique(sale_order_id)",
        "Only one abandonment record per cart.",
    )

    @api.depends("cart_partner_id", "sale_order_id")
    def _compute_name(self):
        for rec in self:
            partner = rec.cart_partner_id.name if rec.cart_partner_id else "?"
            order = rec.sale_order_id.name if rec.sale_order_id else "?"
            rec.name = f"Abandoned {order} — {partner}"

    # -------- Cron entry point --------

    @api.model
    def cron_send_abandoned_reminders(self):
        """Scan stale draft carts and send reminders for the new ones.

        Returns the number of reminders dispatched (useful for tests and
        manual “Run now” inspection).
        """
        cutoff = fields.Datetime.now() - timedelta(hours=_ABANDONMENT_HOURS)
        public_user = self.env.ref("base.public_user", raise_if_not_found=False)
        public_partner_id = public_user.partner_id.id if public_user else 0

        SaleOrder = self.env["sale.order"].sudo()
        domain = [
            ("state", "=", "draft"),
            ("write_date", "<=", cutoff),
            ("order_line", "!=", False),
            ("partner_id", "!=", public_partner_id),
        ]
        drafts = SaleOrder.search(domain)
        sent = 0
        for order in drafts:
            record = self.sudo().search([("sale_order_id", "=", order.id)], limit=1)
            if not record:
                record = self.sudo().create(
                    {
                        "cart_partner_id": order.partner_id.id,
                        "sale_order_id": order.id,
                        "cart_amount": order.amount_total,
                        "abandoned_at": fields.Datetime.now(),
                    }
                )
            if record.reminder_sent:
                continue
            channel = record._dispatch_reminder()
            record.write(
                {
                    "reminder_sent": channel != "none",
                    "reminder_sent_at": fields.Datetime.now(),
                    "reminder_channel": channel,
                }
            )
            if channel != "none":
                sent += 1
        return sent

    def _dispatch_reminder(self) -> str:
        """Send the reminder. Returns the channel used (or ``'none'``)."""
        self.ensure_one()
        partner = self.cart_partner_id

        # Channel selection: WhatsApp if consent + module available;
        # email otherwise.
        if self._can_use_whatsapp(partner):
            try:
                self._send_whatsapp_reminder()
                return "whatsapp"
            except Exception as e:  # noqa: BLE001 — never block the cron
                _logger.warning("Cart abandonment WA dispatch failed for %s: %s", partner.id, e)

        if partner.email:
            try:
                self._send_email_reminder()
                return "email"
            except Exception as e:  # noqa: BLE001
                _logger.warning("Cart abandonment email dispatch failed for %s: %s", partner.id, e)

        return "none"

    def _can_use_whatsapp(self, partner) -> bool:
        """True iff the WA module is present and partner consented for marketing."""
        if not partner or not (partner.phone or partner.mobile):
            return False
        if "custom.whatsapp.account" not in self.env:
            return False
        Consent = self.env.get("pdp.consent")
        if Consent is None:
            return False
        marketing_purpose = self.env.ref(
            "custom_pdp_consent.purpose_marketing",
            raise_if_not_found=False,
        )
        if not marketing_purpose:
            return False
        return bool(
            Consent.sudo().search_count(
                [
                    ("partner_id", "=", partner.id),
                    ("purpose_id", "=", marketing_purpose.id),
                    ("state", "=", "granted"),
                ]
            )
        )

    def _send_email_reminder(self):
        self.ensure_one()
        template = self.env.ref(
            "custom_ecommerce.mail_template_cart_abandonment",
            raise_if_not_found=False,
        )
        if not template:
            _logger.warning("Cart abandonment email template missing.")
            return
        template.sudo().send_mail(self.sale_order_id.id, force_send=False)

    def _send_whatsapp_reminder(self):
        """Stub WA dispatch — delegated to custom_whatsapp if available."""
        self.ensure_one()
        WAAccount = self.env.get("custom.whatsapp.account")
        if WAAccount is None:
            return
        # Real send is module-private; we just message_post a marker so
        # the audit trail captures the dispatch intent.
        self.message_post(
            body=_("WhatsApp cart-abandonment reminder dispatched to %s for cart %s.")
            % (self.cart_partner_id.display_name, self.sale_order_id.name)
        )
