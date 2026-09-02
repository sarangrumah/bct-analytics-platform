# -*- coding: utf-8 -*-
"""Wizard: convert a wrongly-proposed brd.recommendation into a brd.lesson.

Analyst opens a recommendation that the LLM should not have proposed, clicks
"Reject & save as lesson". Wizard pre-fills:
* section_pattern = first 500 chars of all related section contents joined
* rejected_proposals = [recommendation.name]
* source_recommendation_id = recommendation
Analyst fills correct_modules + reason, picks severity, hits Save → the lesson
is created and the recommendation is canceled in one shot.
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BrdRejectAsLessonWizard(models.TransientModel):
    _name = "brd.reject.as.lesson.wizard"
    _description = "Reject Recommendation and Save as Lesson"

    recommendation_id = fields.Many2one("brd.recommendation", required=True, ondelete="cascade")
    name = fields.Char(required=True)
    section_pattern = fields.Text(required=True)
    rejected_proposals = fields.Char(
        help="Comma-separated list of proposal module names to reject.",
    )
    correct_module_ids = fields.Many2many(
        "custom.module.capability.entry",
        string="Use Instead",
    )
    reason = fields.Text(required=True)
    severity = fields.Selection(
        [("blocker", "Blocker"), ("hint", "Hint")],
        default="hint",
        required=True,
    )

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        rec_id = self.env.context.get("default_recommendation_id") or self.env.context.get("active_id")
        if not rec_id:
            return defaults
        Rec = self.env["brd.recommendation"].browse(rec_id)
        if not Rec.exists():
            return defaults
        related = Rec.related_section_ids
        snippet = " | ".join((s.title or "").strip() + ": " + (s.content or "").strip()[:300] for s in related[:3])[
            :500
        ]
        defaults.update(
            {
                "recommendation_id": Rec.id,
                "name": f"Reject {Rec.name}",
                "section_pattern": snippet or (Rec.scope or "")[:500],
                "rejected_proposals": Rec.name or "",
                "reason": f"Rejected manually by analyst on review of BRD '{Rec.document_id.display_name}'.",
            }
        )
        return defaults

    def action_save_lesson(self):
        self.ensure_one()
        if not self.correct_module_ids:
            raise UserError(_("Pick at least one existing module that covers the capability."))
        rejected = [t.strip() for t in (self.rejected_proposals or "").split(",") if t.strip()]
        lesson = self.env["brd.lesson"].create(
            {
                "name": self.name,
                "section_pattern": self.section_pattern,
                "rejected_proposals": rejected,
                "correct_modules": [(6, 0, self.correct_module_ids.ids)],
                "reason": self.reason,
                "severity": self.severity,
                "source_recommendation_id": self.recommendation_id.id,
            }
        )
        self.recommendation_id.write({"state": "canceled"})
        return {
            "type": "ir.actions.act_window",
            "name": _("Lesson Saved"),
            "res_model": "brd.lesson",
            "res_id": lesson.id,
            "view_mode": "form",
            "target": "current",
        }
