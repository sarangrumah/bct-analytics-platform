# -*- coding: utf-8 -*-
"""Newsletter subscribers captured from the storefront signup form."""

from __future__ import annotations

import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class StorefrontSubscriber(models.Model):
    _name = "custom.storefront.subscriber"  # nosemgrep
    _description = "Storefront Newsletter Subscriber"
    _order = "create_date desc"

    email = fields.Char(required=True, index=True)
    active = fields.Boolean(default=True)
    source = fields.Char(default="storefront")

    _email_uniq = models.Constraint(
        "unique(email)",
        "This email is already subscribed.",
    )

    @api.model
    def _storefront_subscribe(self, email):
        """Idempotent subscribe. Newsletter signup = explicit marketing consent."""
        email = (email or "").strip().lower()
        if not _EMAIL_RE.match(email):
            raise ValidationError(_("Please enter a valid email address."))
        existing = self.sudo().with_context(active_test=False).search([("email", "=", email)], limit=1)
        if existing:
            if not existing.active:
                existing.active = True
            return existing
        return self.sudo().create({"email": email})
