# -*- coding: utf-8 -*-
"""One place that knows where a partner's WhatsApp number lives.

Odoo 19 **removed** ``res.partner.mobile``; the number now lives in ``phone``
alone (``phone_mobile_search`` is a search helper, not a stored value). Every
site in this module used to read ``partner.mobile or partner.phone``, which
raises ``AttributeError`` on 19, and the inbound matcher searched a
``('mobile', 'ilike', ...)`` domain, which raises ``ValueError: Invalid field
res.partner.mobile in condition``.

Rather than delete the mobile handling outright, both helpers resolve the field
at runtime. On 19 they use ``phone``; on an older build that still has
``mobile`` they keep preferring it, which is what the callers intended.
"""

from __future__ import annotations

from odoo import models

# Checked in preference order: a mobile number beats a landline for WhatsApp.
_PHONE_FIELDS = ("mobile", "phone")

# Matching an inbound number is a different problem from picking one to send
# to. ``phone`` holds whatever the user typed -- "+62 812-3456-7890" -- so a
# last-9-digits ``ilike`` never matches it once separators are in the way.
# ``phone_sanitized`` (from ``mail``) is the same number in E.164, which is
# what makes the comparison work, so it is tried first.
_SEARCH_PHONE_FIELDS = ("phone_sanitized", "mobile", "phone")


def available_phone_fields(model) -> list[str]:
    """Phone-ish fields to READ from, in preference order.

    Only fields present on this build: Odoo 19 dropped ``mobile``.
    """
    return [f for f in _PHONE_FIELDS if f in model._fields]


def available_phone_search_fields(model) -> list[str]:
    """Phone-ish fields to SEARCH, in preference order.

    Naming a field the registry does not have is a hard ValueError in a
    domain, not a no-match, so the list is filtered against ``_fields``.
    """
    return [f for f in _SEARCH_PHONE_FIELDS if f in model._fields]


class ResPartner(models.Model):
    _inherit = "res.partner"

    def _whatsapp_phone(self) -> str:
        """The number to send to, or "" when the partner has none."""
        self.ensure_one()
        for fname in available_phone_fields(self):
            value = (self[fname] or "").strip()
            if value:
                return value
        return ""
