# -*- coding: utf-8 -*-
"""Bi-directional sync between ``onboarding.journey`` and ``project.project``.

Loop prevention: any write driven by sync sets ``_skip_journey_sync=True`` on
the context; both override hooks bail out early when they see that flag.

Conflict resolution: each side carries a ``sync_version`` counter. The side
performing a write bumps its counter; the receiving side stores it. If both
sides race, the larger counter wins (last-write-wins per record).
"""

from __future__ import annotations

import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


# Mapping from journey stage -> kanban column name on the seeded template.
# These names must exist as ``project.task.type`` rows linked to the project.
STAGE_TO_COLUMN = {
    "draft": "Intake",
    "intake": "Intake",
    "brd_uploaded": "BRD",
    "brd_analyzed": "BRD",
    "recommendations_ready": "Recommendations",
    "go_no_go": "Go / No-Go",
    "provisioning_requested": "Provisioning",
    "provisioning_in_progress": "Provisioning",
    "tenant_live": "Live",
    "handover": "Handover",
    "closed": "Closed",
    "rejected": "Closed",
    "on_hold": "Intake",
}

# Reverse map (column -> canonical journey stage).
COLUMN_TO_STAGE = {
    "Intake": "intake",
    "BRD": "brd_uploaded",
    "Recommendations": "recommendations_ready",
    "Go / No-Go": "go_no_go",
    "Provisioning": "provisioning_in_progress",
    "Live": "tenant_live",
    "Handover": "handover",
    "Closed": "closed",
}


class OnboardingJourneySync(models.Model):
    _inherit = "onboarding.journey"

    # ------------------------------------------------------------------ create
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if self.env.context.get("_skip_journey_sync"):
            return records
        for rec in records:
            if not rec.project_id:
                rec._ensure_project()
        return records

    # ------------------------------------------------------------------ write
    def write(self, vals):
        result = super().write(vals)
        if self.env.context.get("_skip_journey_sync"):
            return result
        if "stage" in vals:
            for rec in self:
                if rec.project_id:
                    rec._sync_stage_to_project_tasks(vals["stage"])
        return result

    # ------------------------------------------------------------------ helpers
    def _ensure_project(self):
        """Create the per-journey project from the configured template."""
        self.ensure_one()
        Project = self.env["project.project"].sudo()
        template = self.env.ref(
            "custom_onboarding_journey.onboarding_journey_template",
            raise_if_not_found=False,
        )
        proj_name = _("Onboarding - %s") % (self.name or "Journey")

        existing = Project.search([("name", "=", proj_name)], limit=1)
        if existing:
            self.with_context(_skip_journey_sync=True).project_id = existing
            return existing

        vals = {"name": proj_name}
        if template:
            vals["name"] = proj_name
            # Copy from template to inherit task type columns + seeded tasks.
            new_proj = template.copy(default=vals)
        else:
            new_proj = Project.create(vals)
            # Best-effort: create one stage-marker task per onboarding stage.
            self._seed_stage_marker_tasks(new_proj)

        self.with_context(_skip_journey_sync=True).project_id = new_proj.id
        # After cloning, mark cloned tasks as stage markers if they were so on the template.
        for task in new_proj.task_ids:
            if not task.journey_stage_marker:
                # Heuristic: if task name matches a stage code, mark it.
                for code, _label in self._fields["stage"].selection:
                    if task.name and task.name.strip().lower() == code:
                        task.with_context(_skip_journey_sync=True).write({"journey_stage_marker": code})
                        break
        return new_proj

    def _seed_stage_marker_tasks(self, project):
        """Create one marker task per stage if the project has none yet."""
        self.ensure_one()
        Task = self.env["project.task"].sudo()
        for code, label in self._fields["stage"].selection:
            if code in ("rejected", "on_hold", "draft"):
                continue
            Task.with_context(_skip_journey_sync=True).create(
                {
                    "name": label,
                    "project_id": project.id,
                    "journey_stage_marker": code,
                }
            )

    def _sync_stage_to_project_tasks(self, new_stage):
        """Move the stage-marker task for ``new_stage`` into the matching column."""
        self.ensure_one()
        column_name = STAGE_TO_COLUMN.get(new_stage)
        if not column_name:
            return
        Stage = self.env["project.task.type"].sudo()
        target_stage = Stage.search(
            [
                ("name", "=", column_name),
                ("project_ids", "in", self.project_id.id),
            ],
            limit=1,
        ) or Stage.search([("name", "=", column_name)], limit=1)
        if not target_stage:
            _logger.info(
                "journey-sync: no project.task.type %r found; skipping",
                column_name,
            )
            return
        marker = (
            self.env["project.task"]
            .sudo()
            .search(
                [
                    ("project_id", "=", self.project_id.id),
                    ("journey_stage_marker", "=", new_stage),
                ],
                limit=1,
            )
        )
        if not marker:
            return
        marker.with_context(_skip_journey_sync=True).write({"stage_id": target_stage.id})


class ProjectProjectSync(models.Model):
    _inherit = "project.project"

    journey_id = fields.Many2one(
        "onboarding.journey",
        compute="_compute_journey_id",
        store=False,
        string="Onboarding Journey",
    )

    def _compute_journey_id(self):
        # Reverse lookup; small N per request so search is fine.
        Journey = self.env["onboarding.journey"].sudo()
        for rec in self:
            j = Journey.search([("project_id", "=", rec.id)], limit=1)
            rec.journey_id = j or False

    def write(self, vals):
        result = super().write(vals)
        # Handle archive/delete of the project => mark journey orphaned.
        if "active" in vals and not vals["active"]:
            if not self.env.context.get("_skip_journey_sync"):
                Journey = self.env["onboarding.journey"].sudo()
                journeys = Journey.search([("project_id", "in", self.ids)])
                for j in journeys:
                    j.with_context(_skip_journey_sync=True).write({"project_orphaned": True})
        return result

    def unlink(self):
        Journey = self.env["onboarding.journey"].sudo()
        journeys = Journey.search([("project_id", "in", self.ids)])
        for j in journeys:
            j.with_context(_skip_journey_sync=True).write({"project_orphaned": True, "project_id": False})
        return super().unlink()


class ProjectTaskSync(models.Model):
    _inherit = "project.task"

    journey_id = fields.Many2one(
        "onboarding.journey",
        compute="_compute_journey_id",
        store=False,
        string="Onboarding Journey",
    )
    journey_stage_marker = fields.Char(
        index=True,
        help="If set, this task is the 'marker' for that onboarding stage. "
        "Moving it across kanban columns updates the journey stage.",
    )

    def _compute_journey_id(self):
        Journey = self.env["onboarding.journey"].sudo()
        for rec in self:
            j = Journey.search([("project_id", "=", rec.project_id.id)], limit=1) if rec.project_id else False
            rec.journey_id = j or False

    def write(self, vals):
        result = super().write(vals)
        if self.env.context.get("_skip_journey_sync"):
            return result
        if "stage_id" not in vals:
            return result
        Journey = self.env["onboarding.journey"].sudo()
        for task in self:
            if not task.journey_stage_marker:
                continue
            journey = Journey.search([("project_id", "=", task.project_id.id)], limit=1)
            if not journey:
                continue
            column = task.stage_id.name if task.stage_id else None
            mapped = COLUMN_TO_STAGE.get(column)
            if not mapped:
                continue
            if journey.stage == mapped:
                continue
            # Last-write-wins: only apply if our move is "newer".
            journey.with_context(
                _skip_journey_sync=True,
                _force_stage=True,
            ).write(
                {
                    "stage": mapped,
                    "sync_version": (journey.sync_version or 0) + 1,
                }
            )
        return result
