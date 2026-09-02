# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib

from odoo import api, fields, models


class CustomAffiliateClick(models.Model):
    _name = "custom.affiliate.click"  # nosemgrep
    _description = "Affiliate Click"
    _order = "create_date desc"

    affiliate_id = fields.Many2one(
        "custom.affiliate", string="Affiliate", required=True, ondelete="cascade", index=True
    )
    link_id = fields.Many2one("custom.affiliate.link", string="Link", ondelete="set null")
    landing_url = fields.Char(string="Landing URL")
    referrer = fields.Char(string="Referrer")
    session_key = fields.Char(string="Session", index=True)
    ip_hash = fields.Char(string="IP (hashed)")
    ua_hash = fields.Char(string="User-Agent (hashed)")

    @api.model
    def _hash(self, value):
        if not value:
            return False
        return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()[:32]

    @api.model
    def _record_click(
        self,
        affiliate,
        link=None,
        landing_url=None,
        referrer=None,
        session_key=None,
        ip=None,
        user_agent=None,
        dedup_seconds=60,
    ):
        """Write a click row, de-duplicating rapid repeats per (affiliate, session)."""
        if dedup_seconds and session_key:
            recent = self.sudo().search(
                [
                    ("affiliate_id", "=", affiliate.id),
                    ("session_key", "=", session_key),
                    ("create_date", ">=", fields.Datetime.subtract(fields.Datetime.now(), seconds=dedup_seconds)),
                ],
                limit=1,
            )
            if recent:
                return recent
        return self.sudo().create(
            {
                "affiliate_id": affiliate.id,
                "link_id": link.id if link else False,
                "landing_url": (landing_url or "")[:500],
                "referrer": (referrer or "")[:500],
                "session_key": session_key or False,
                "ip_hash": self._hash(ip),
                "ua_hash": self._hash(user_agent),
            }
        )
