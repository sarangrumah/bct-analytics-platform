# -*- coding: utf-8 -*-
"""Refresh tokens, stored hashed. The plaintext exists only in the HTTP response."""

import hashlib
import secrets

from odoo import api, fields, models

REFRESH_TTL_DAYS = 14


class CustomPmoToken(models.Model):
    _name = "custom.pmo.token"
    _description = "VAS PMO Refresh Token"
    _order = "create_date desc"

    user_id = fields.Many2one("res.users", required=True, ondelete="cascade", index=True)
    token_hash = fields.Char(required=True, index=True)
    ua_hash = fields.Char()
    ip_hash = fields.Char()
    expires_at = fields.Datetime(required=True)
    revoked = fields.Boolean(default=False)

    @api.model
    def _hash(self, value):
        return hashlib.sha256((value or "").encode("utf-8")).hexdigest()

    @api.model
    def _issue(self, user, user_agent=None, ip=None):
        raw = secrets.token_urlsafe(48)
        self.create(
            {
                "user_id": user.id,
                "token_hash": self._hash(raw),
                "ua_hash": self._hash(user_agent) if user_agent else False,
                "ip_hash": self._hash(ip) if ip else False,
                "expires_at": fields.Datetime.add(fields.Datetime.now(), days=REFRESH_TTL_DAYS),
            }
        )
        return raw

    @api.model
    def _resolve(self, raw):
        if not raw:
            return self.browse()
        return self.search(
            [
                ("token_hash", "=", self._hash(raw)),
                ("revoked", "=", False),
                ("expires_at", ">", fields.Datetime.now()),
            ],
            limit=1,
        )

    def _rotate(self, user_agent=None, ip=None):
        """Single-use refresh: the presented token dies as the new one is born."""
        self.ensure_one()
        self.revoked = True
        return self._issue(self.user_id, user_agent, ip)

    @api.model
    def cron_purge(self):
        expired = self.search([("expires_at", "<", fields.Datetime.now())])
        expired.unlink()
