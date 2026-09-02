# -*- coding: utf-8 -*-
"""Lessons learned — analyst corrections persisted as injectable hints.

When an analyst rejects a wrong recommendation (e.g. "custom_drone_rental was
proposed but custom_rental already covers it"), the correction is saved as a
``brd.lesson`` record. The analyzer reads active lessons at each run and
injects relevant ones into the system prompt so the LLM doesn't repeat the
same mistake.

Two severities:
* ``blocker`` — always injected (e.g. "never propose modules whose name
  starts with custom_drone_; covered by custom_rental").
* ``hint`` — injected only when keywords from the lesson's section_pattern
  overlap with the current BRD's content. Cheap substring match keeps
  irrelevant lessons out of the prompt.
"""

from __future__ import annotations

from odoo import fields, models


class BrdLesson(models.Model):
    _name = "brd.lesson"
    _inherit = ["mail.thread"]
    _description = "BRD Analyzer Lesson Learned"
    _order = "severity, create_date desc"
    _rec_name = "name"

    name = fields.Char(
        required=True,
        help="Short, human-friendly label (shown in the prompt).",
    )
    section_pattern = fields.Text(
        required=True,
        help="Keywords or phrases the lesson applies to. Whitespace-split; "
        "tokens of length >= 4 are used for matching against BRD section "
        "content (case-insensitive substring).",
    )
    rejected_proposals = fields.Json(
        default=list,
        help="JSON list of module-name strings the LLM was wrongly proposing, "
        'e.g. ["custom_drone_rental", "custom_tenant_bridge"].',
    )
    correct_modules = fields.Many2many(
        "custom.module.capability.entry",
        "brd_lesson_correct_module_rel",
        "lesson_id",
        "module_id",
        string="Use Instead",
        help="The existing modules that already cover the capability the "
        "LLM was trying to satisfy with a new proposal.",
    )
    reason = fields.Text(
        required=True,
        help="Short paragraph explaining WHY the lesson exists. Becomes the rationale visible to the LLM.",
    )
    severity = fields.Selection(
        [
            ("blocker", "Blocker (always injected)"),
            ("hint", "Hint (only when section keywords match)"),
        ],
        default="hint",
        required=True,
        tracking=True,
    )
    active = fields.Boolean(default=True, tracking=True)
    source_recommendation_id = fields.Many2one(
        "brd.recommendation",
        ondelete="set null",
        copy=False,
        help="Optional link to the recommendation that birthed this lesson.",
    )
    created_by_lesson = fields.Many2one(
        "res.users",
        default=lambda self: self.env.user,
        readonly=True,
    )
    inject_count = fields.Integer(
        default=0,
        readonly=True,
        help="Number of analyzer runs this lesson has been injected into. "
        "Lets ops gauge which lessons are pulling weight.",
    )

    def action_archive(self):
        self.write({"active": False})

    def action_unarchive(self):
        self.write({"active": True})
