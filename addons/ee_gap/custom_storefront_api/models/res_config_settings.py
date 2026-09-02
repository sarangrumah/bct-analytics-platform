# -*- coding: utf-8 -*-
"""Storefront payment configuration, surfaced in the Custom Platform settings.

Which providers appear at storefront checkout is driven by the stock
``payment.provider.is_published`` flag (toggled on each provider form). This
panel holds the storefront-wide switches — a master enable and the default
provider — and links out to the provider list and the transfer-proof inbox.
"""

from __future__ import annotations

from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    custom_storefront_payments_enabled = fields.Boolean(
        string="Enable Storefront Payments",
        config_parameter="custom_storefront_api.payments_enabled",
        default=True,
    )
    custom_storefront_default_provider = fields.Selection(
        [
            ("", "First published provider"),
            ("custom", "Manual Bank Transfer"),
            ("midtrans", "Midtrans"),
            ("xendit", "Xendit"),
            ("doku", "DOKU"),
            ("pos", "POS"),
        ],
        string="Default Payment Method",
        config_parameter="custom_storefront_api.default_provider",
        help="Pre-selected method at storefront checkout. Falls back to the "
        "first published provider when empty or unavailable.",
    )
    custom_storefront_published_summary = fields.Html(
        string="Published Providers",
        compute="_compute_custom_storefront_published_summary",
        sanitize=False,
    )

    @api.depends("custom_storefront_payments_enabled")
    def _compute_custom_storefront_published_summary(self):
        Provider = self.env["payment.provider"].sudo()
        for rec in self:
            providers = Provider.search(
                [("state", "in", ("enabled", "test")), ("is_published", "=", True)],
                order="sequence, id",
            )
            if not providers:
                rec.custom_storefront_published_summary = (
                    "<div class='alert alert-warning' role='alert'>"
                    "No payment provider is published to the storefront yet. "
                    "Open <b>Payment Providers</b>, enable a provider and toggle "
                    "<b>Published</b> so it shows at checkout. Manual Bank Transfer "
                    "(Wire Transfer) works with no credentials."
                    "</div>"
                )
                continue
            items = "".join(
                "<li><b>%s</b> — %s%s</li>"
                % (
                    p.name,
                    "Manual transfer" if p.code == "custom" else "Redirect gateway (%s)" % p.code,
                    " · <i>test/sandbox</i>" if p.state == "test" else "",
                )
                for p in providers
            )
            rec.custom_storefront_published_summary = (
                "<div class='alert alert-info' role='alert'>"
                "<p class='mb-1'>Methods currently shown at storefront checkout:</p>"
                "<ul class='mb-0'>%s</ul></div>" % items
            )

    def action_open_payment_providers(self):
        self.ensure_one()
        return self.env["ir.actions.actions"]._for_xml_id("payment.action_payment_provider")

    def action_open_transfer_proofs(self):
        self.ensure_one()
        return self.env["ir.actions.actions"]._for_xml_id("custom_storefront_api.action_payment_proof")
