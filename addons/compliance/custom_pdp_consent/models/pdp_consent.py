# -*- coding: utf-8 -*-
"""Subject consent records (audited)."""

from __future__ import annotations

import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class PdpConsent(models.Model):
    _name = "pdp.consent"
    _description = "PDP Consent"
    _order = "given_at desc, id desc"
    _inherit = ["pdp.audited.mixin"]

    partner_id = fields.Many2one("res.partner", required=True, ondelete="cascade", index=True)
    purpose_id = fields.Many2one("pdp.consent.purpose", required=True, ondelete="restrict", index=True)
    purpose_code = fields.Char(related="purpose_id.code", store=True)
    given_at = fields.Datetime(default=fields.Datetime.now, required=True)
    expires_at = fields.Datetime(compute="_compute_expires_at", store=True)
    withdrawn_at = fields.Datetime()
    evidence = fields.Binary(attachment=True, help="Signed form, screenshot, or any evidence document.")
    evidence_filename = fields.Char()
    version = fields.Char(default="1.0")
    notes = fields.Text()
    state = fields.Selection(
        [
            ("active", "Active"),
            ("expired", "Expired"),
            ("withdrawn", "Withdrawn"),
        ],
        compute="_compute_state",
        store=True,
    )

    _partner_purpose_unique_active = models.Constraint(
        "EXCLUDE (partner_id WITH =, purpose_id WITH =) WHERE (withdrawn_at IS NULL)",
        "Active consent already exists for this partner and purpose.",
    )

    @api.depends("given_at", "purpose_id.requires_renewal_days")
    def _compute_expires_at(self):
        for rec in self:
            if rec.given_at and rec.purpose_id and rec.purpose_id.requires_renewal_days:
                rec.expires_at = rec.given_at + timedelta(days=rec.purpose_id.requires_renewal_days)
            else:
                rec.expires_at = False

    @api.depends("withdrawn_at", "expires_at")
    def _compute_state(self):
        now = fields.Datetime.now()
        for rec in self:
            if rec.withdrawn_at:
                rec.state = "withdrawn"
            elif rec.expires_at and rec.expires_at < now:
                rec.state = "expired"
            else:
                rec.state = "active"

    # ---------- public API ----------

    @api.model
    def check_consent(self, partner, purpose_code: str) -> bool:
        """Return True if `partner` has an active consent for `purpose_code`."""
        if not partner:
            return False
        partner_id = partner.id if hasattr(partner, "id") else int(partner)
        rec = self.sudo().search(
            [
                ("partner_id", "=", partner_id),
                ("purpose_code", "=", purpose_code),
                ("withdrawn_at", "=", False),
            ],
            limit=1,
        )
        if not rec:
            return False
        if rec.expires_at and rec.expires_at < fields.Datetime.now():
            return False
        return True

    def action_withdraw(self, reason: str | None = None):
        for rec in self:
            if rec.withdrawn_at:
                continue
            rec.withdrawn_at = fields.Datetime.now()
            rec._pdp_audit_write(
                "consent_withdraw",
                rec.id,
                {"purpose": rec.purpose_code, "partner_id": rec.partner_id.id},
                reason=reason or "Consent withdrawn by subject",
            )
        return True

    # ---------- ORM overrides: audit grant ----------

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        for rec in recs:
            rec._pdp_audit_write(
                "consent_grant",
                rec.id,
                {
                    "purpose": rec.purpose_code,
                    "partner_id": rec.partner_id.id,
                    "version": rec.version,
                },
                reason="Consent granted",
            )
        return recs
