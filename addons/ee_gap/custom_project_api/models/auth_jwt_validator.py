# -*- coding: utf-8 -*-
"""Resolve the JWT to the real internal user.

The storefront can afford ``user_id_strategy = static``: every caller there is a customer
and writes go through sudo. Here the caller is a member of the team, and the audit trail
has to name them, so the token's ``sub`` claim carries the login and this strategy turns it
back into a ``res.users``.
"""

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class AuthJwtValidator(models.Model):
    _inherit = "auth.jwt.validator"

    user_id_strategy = fields.Selection(
        selection_add=[("pmo_login", "VAS PMO — from login claim")],
        ondelete={"pmo_login": "set default"},
    )

    @api.model
    def _pmo_resolve_user(self, payload):
        login = payload.get("sub") or payload.get("login")
        if not login:
            return None
        user = (
            self.env["res.users"]
            .sudo()
            .search(
                [("login", "=", login), ("active", "=", True)],
                limit=1,
            )
        )
        if not user:
            _logger.info("VAS PMO: JWT sub %s does not match an active user", login)
            return None
        return user

    def _get_uid(self, payload):
        if self.user_id_strategy == "pmo_login":
            user = self._pmo_resolve_user(payload)
            return user.id if user else None
        return super()._get_uid(payload)
