# -*- coding: utf-8 -*-
"""Go/No-Go decision wizard."""

from __future__ import annotations

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class OnboardingGoNoGoWizard(models.TransientModel):
    _name = "onboarding.go.no.go.wizard"
    _description = "Onboarding Go/No-Go Wizard"

    journey_id = fields.Many2one(
        "onboarding.journey",
        required=True,
        ondelete="cascade",
        default=lambda self: self.env.context.get("default_journey_id"),
    )
    decision = fields.Selection(
        [("go", "Go"), ("no_go", "No Go")],
        required=True,
        default="go",
    )
    rejection_reason = fields.Text()

    @api.constrains("decision", "rejection_reason")
    def _check_reason(self):
        for rec in self:
            if rec.decision == "no_go" and not (rec.rejection_reason or "").strip():
                raise UserError(_("Rejection reason is required for No-Go."))

    def action_decide(self):
        self.ensure_one()
        journey = self.journey_id
        if self.decision == "no_go":
            journey.with_context(_transition_notes=self.rejection_reason or "No-Go").write({"stage": "rejected"})
            return {"type": "ir.actions.act_window_close"}

        # Go path: create approval.request, link, advance stage.
        Matrix = self.env.get("approval.matrix")
        Request = self.env.get("approval.request")
        approval = False
        if Matrix and Request:
            matrices = Matrix.sudo().search(
                [("model_id.model", "=", "onboarding.journey"), ("active", "=", True)],
                order="priority desc, sequence asc",
                limit=1,
            )
            if matrices:
                approval = Request.sudo().create(
                    {
                        "matrix_id": matrices.id,
                        "res_model": "onboarding.journey",
                        "res_id": journey.id,
                    }
                )
                journey.approval_request_id = approval.id
            else:
                _logger.info(
                    "go-no-go: no approval matrix for onboarding.journey; skipping",
                )
        journey.with_context(_transition_notes="Go decision").write({"stage": "go_no_go"})
        return {
            "type": "ir.actions.act_window",
            "res_model": "onboarding.journey",
            "res_id": journey.id,
            "view_mode": "form",
        }
