# -*- coding: utf-8 -*-
"""Wizard to attach BRD files to a journey and kick off extraction + AI."""

from __future__ import annotations

import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class OnboardingBrdUploadWizard(models.TransientModel):
    _name = "onboarding.brd.upload.wizard"
    _description = "Onboarding BRD Upload Wizard"

    journey_id = fields.Many2one(
        "onboarding.journey",
        required=True,
        ondelete="cascade",
    )
    attachment_ids = fields.Many2many(
        "ir.attachment",
        string="BRD Files",
        required=True,
    )

    def action_upload(self):
        self.ensure_one()
        if not self.attachment_ids:
            raise UserError(_("Attach at least one BRD file."))
        BrdDocument = self.env["brd.document"]
        created = self.env["brd.document"]
        for att in self.attachment_ids:
            doc = BrdDocument.create(
                {
                    "name": att.name or _("BRD"),
                    "document_attachment_id": att.id,
                    "journey_id": self.journey_id.id,
                    "owner_user_id": self.env.user.id,
                }
            )
            created |= doc
            # Best-effort extraction + analysis (sync — small files only).
            try:
                doc.action_extract()
            except Exception as exc:
                _logger.warning("BRD extract failed for doc %s: %s", doc.id, exc)
            try:
                doc.action_analyze()
            except Exception as exc:
                _logger.warning("BRD analyze failed for doc %s: %s", doc.id, exc)

        # Move journey forward.
        if self.journey_id.stage in ("draft", "intake"):
            self.journey_id.with_context(_transition_notes="BRD(s) uploaded via wizard").write(
                {"stage": "brd_uploaded"}
            )

        return {
            "type": "ir.actions.act_window",
            "res_model": "onboarding.journey",
            "res_id": self.journey_id.id,
            "view_mode": "form",
        }
