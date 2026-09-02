# -*- coding: utf-8 -*-
"""Append-only audit log of every stage transition for an onboarding journey."""

from odoo import api, fields, models
from odoo.exceptions import AccessError


class OnboardingStageTransition(models.Model):
    _name = "onboarding.stage.transition"
    _description = "Onboarding Journey Stage Transition (append-only)"
    _order = "transitioned_at desc, id desc"
    _rec_name = "display_name"

    journey_id = fields.Many2one(
        "onboarding.journey",
        required=True,
        ondelete="cascade",
        index=True,
    )
    from_stage = fields.Char(required=False)
    to_stage = fields.Char(required=True)
    user_id = fields.Many2one(
        "res.users",
        required=True,
        default=lambda self: self.env.user,
    )
    transitioned_at = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        index=True,
    )
    notes = fields.Text()
    display_name = fields.Char(compute="_compute_display_name", store=True)

    @api.depends("journey_id", "from_stage", "to_stage")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"{rec.journey_id.name or '?'}: {rec.from_stage or '-'} -> {rec.to_stage}"

    def write(self, vals):
        # Append-only: forbid any mutation after creation.
        raise AccessError("onboarding.stage.transition is append-only; create a new row instead.")

    def unlink(self):
        # Allow only via cascade from the journey itself (admin cleanup).
        if not self.env.is_superuser():
            raise AccessError("Stage transitions cannot be deleted.")
        return super().unlink()
