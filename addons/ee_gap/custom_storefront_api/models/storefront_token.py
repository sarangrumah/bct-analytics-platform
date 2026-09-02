# -*- coding: utf-8 -*-
"""Refresh-token registry for storefront customer sessions.

The short-lived JWT *access* token is stateless (verified by ``auth_jwt``).
The *refresh* token is an opaque high-entropy string handed to the BFF and
kept in an HttpOnly cookie; only its SHA-256 hash is stored here so a DB
leak never yields usable tokens. Rotating on every refresh + a revoke flag
gives us logout / session-revocation without a stateful access token.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from odoo import api, fields, models


class StorefrontToken(models.Model):
    _name = "custom.storefront.token"  # nosemgrep
    _description = "Storefront Refresh Token"
    _order = "id desc"

    partner_id = fields.Many2one("res.partner", required=True, ondelete="cascade", index=True)
    user_id = fields.Many2one("res.users", ondelete="cascade", index=True)
    token_hash = fields.Char(required=True, index=True)
    expires_at = fields.Datetime(required=True)
    revoked = fields.Boolean(default=False, index=True)
    last_used = fields.Datetime()
    user_agent = fields.Char()
    client_ip = fields.Char()

    @api.model
    def _hash(self, raw: str) -> str:
        return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()

    @api.model
    def _issue(self, partner, user, user_agent=None, client_ip=None, days=30) -> str:
        """Create a refresh-token row, return the RAW token (shown once)."""
        raw = secrets.token_urlsafe(48)
        self.sudo().create(
            {
                "partner_id": partner.id,
                "user_id": user.id if user else False,
                "token_hash": self._hash(raw),
                "expires_at": fields.Datetime.now() + timedelta(days=days),
                "user_agent": (user_agent or "")[:256] or False,
                "client_ip": (client_ip or "")[:64] or False,
            }
        )
        return raw

    @api.model
    def _resolve(self, raw: str):
        """Return the live (non-revoked, non-expired) token row, or empty."""
        if not raw:
            return self.browse()
        rec = self.sudo().search([("token_hash", "=", self._hash(raw)), ("revoked", "=", False)], limit=1)
        if not rec:
            return self.browse()
        if rec.expires_at and rec.expires_at < fields.Datetime.now():
            return self.browse()
        return rec

    def _rotate(self, user_agent=None, client_ip=None) -> str:
        """Revoke this token and mint a fresh one for the same partner."""
        self.ensure_one()
        self.sudo().write({"revoked": True, "last_used": fields.Datetime.now()})
        return self._issue(self.partner_id, self.user_id, user_agent, client_ip)

    def _revoke(self):
        self.sudo().write({"revoked": True})
