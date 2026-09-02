# -*- coding: utf-8 -*-
"""Central state machine for a tenant onboarding lifecycle.

Stages (linear with two terminal branches):

    draft -> intake -> brd_uploaded -> brd_analyzed -> recommendations_ready
          -> go_no_go -> provisioning_requested -> provisioning_in_progress
          -> tenant_live -> handover -> closed

    (any stage) -> rejected            (terminal)
    (any stage) -> on_hold -> (resume to previous stage)
"""

from __future__ import annotations

import json
import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


STAGE_SELECTION = [
    ("draft", "Draft"),
    ("intake", "Intake"),
    ("brd_uploaded", "BRD Uploaded"),
    ("brd_analyzed", "BRD Analyzed"),
    ("recommendations_ready", "Recommendations Ready"),
    ("go_no_go", "Go / No-Go"),
    ("provisioning_requested", "Provisioning Requested"),
    ("provisioning_in_progress", "Provisioning In Progress"),
    ("tenant_live", "Tenant Live"),
    ("handover", "Handover"),
    ("closed", "Closed"),
    ("rejected", "Rejected"),
    ("on_hold", "On Hold"),
]

# Allowed transitions (forward graph). on_hold/rejected can be entered from
# almost any stage; closed is terminal.
_FORWARD = {
    "draft": {"intake", "rejected", "on_hold"},
    "intake": {"brd_uploaded", "rejected", "on_hold"},
    "brd_uploaded": {"brd_analyzed", "rejected", "on_hold"},
    "brd_analyzed": {"recommendations_ready", "rejected", "on_hold"},
    "recommendations_ready": {"go_no_go", "rejected", "on_hold"},
    "go_no_go": {"provisioning_requested", "rejected", "on_hold"},
    "provisioning_requested": {"provisioning_in_progress", "rejected", "on_hold"},
    "provisioning_in_progress": {"tenant_live", "rejected", "on_hold"},
    "tenant_live": {"handover", "on_hold"},
    "handover": {"closed", "on_hold"},
    "closed": set(),
    "rejected": set(),
    # on_hold can resume to any non-terminal stage
    "on_hold": {s for s, _l in STAGE_SELECTION if s not in ("closed", "rejected", "on_hold")},
}


def _gen_public_token() -> str:
    return secrets.token_urlsafe(24)


class OnboardingJourney(models.Model):
    _name = "onboarding.journey"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Tenant Onboarding Journey"
    _order = "create_date desc"

    # ------------------------------------------------------------------ core
    name = fields.Char(
        required=True,
        default=lambda self: self.env._("New Onboarding"),
        tracking=True,
        index=True,
    )
    stage = fields.Selection(
        STAGE_SELECTION,
        required=True,
        default="draft",
        tracking=True,
        index=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Prospect / Customer",
        ondelete="restrict",
        tracking=True,
    )

    # ------------------------------------------------------------------ related entities
    brd_document_ids = fields.One2many(
        "brd.document",
        "journey_id",
        string="BRD Documents",
    )
    brd_recommendation_ids = fields.One2many(
        "brd.recommendation",
        related="brd_document_ids.recommendation_ids",
        string="Recommendations",
        readonly=True,
    )
    approval_request_id = fields.Many2one(
        "approval.request",
        string="Go/No-Go Approval",
        ondelete="set null",
        copy=False,
    )
    tenant_registry_id = fields.Many2one(
        "tenant.registry",
        string="Tenant",
        ondelete="set null",
        copy=False,
    )
    # Forward references: tenant_infra / dev_cycle may or may not be present
    # at the journey side; comodel by string is safe.
    tenant_vps_id = fields.Many2one(
        "tenant.vps",
        string="Tenant VPS",
        ondelete="set null",
        copy=False,
    )
    tenant_environment_id = fields.Many2one(
        "tenant.environment",
        string="Tenant Environment",
        ondelete="set null",
        copy=False,
    )
    # dev_cycle_id (M2O dev.cycle) is injected by custom_dev_cycle.
    project_id = fields.Many2one(
        "project.project",
        string="Onboarding Project",
        ondelete="set null",
        copy=False,
        index=True,
    )
    project_orphaned = fields.Boolean(
        default=False,
        copy=False,
        help="Set when the linked project is archived or deleted; the journey survives.",
    )

    # ------------------------------------------------------------------ planning
    mandays_estimate = fields.Integer(
        compute="_compute_mandays_estimate",
        store=True,
        help="Sum of estimated_md across all BRD recommendations on this journey.",
    )
    target_go_live = fields.Date(tracking=True)
    owner_id = fields.Many2one(
        "res.users",
        string="Owner",
        default=lambda self: self.env.user,
        tracking=True,
    )
    ba_id = fields.Many2one(
        "res.users",
        string="Business Analyst",
        tracking=True,
    )

    # ------------------------------------------------------------------ payload
    company_profile_json = fields.Text(
        help='JSON profile captured at intake: {"name", "logo_url", "npwp", '
        '"bank": {...}, "modules_wishlist": [...], "narrative": "..."}.',
    )

    # ------------------------------------------------------------------ public + sync metadata
    public_status_token = fields.Char(
        copy=False,
        index=True,
        default=lambda self: _gen_public_token(),
        help="URL-safe token used by /onboarding/public/status/<token>.",
    )
    sync_version = fields.Integer(
        default=0,
        copy=False,
        help="Bumped on every stage write to drive last-write-wins resolution against the linked project tasks.",
    )

    transition_ids = fields.One2many(
        "onboarding.stage.transition",
        "journey_id",
        string="Stage Transitions",
        readonly=True,
    )
    progress_pct = fields.Integer(
        compute="_compute_progress_pct",
        store=True,
    )

    _public_status_token_uniq = models.Constraint(
        "unique(public_status_token)",
        "Each onboarding journey must have a unique public status token.",
    )

    # ------------------------------------------------------------------ computes
    @api.depends("brd_recommendation_ids.estimated_md")
    def _compute_mandays_estimate(self):
        for rec in self:
            rec.mandays_estimate = sum((r.estimated_md or 0) for r in rec.brd_recommendation_ids)

    @api.depends("stage")
    def _compute_progress_pct(self):
        # Linear stage index over the happy path.
        happy = [
            "draft",
            "intake",
            "brd_uploaded",
            "brd_analyzed",
            "recommendations_ready",
            "go_no_go",
            "provisioning_requested",
            "provisioning_in_progress",
            "tenant_live",
            "handover",
            "closed",
        ]
        for rec in self:
            if rec.stage == "rejected":
                rec.progress_pct = 0
            elif rec.stage == "on_hold":
                rec.progress_pct = 0
            elif rec.stage in happy:
                rec.progress_pct = int(round((happy.index(rec.stage) / (len(happy) - 1)) * 100))
            else:
                rec.progress_pct = 0

    # ------------------------------------------------------------------ CRUD
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("public_status_token"):
                vals["public_status_token"] = _gen_public_token()
        records = super().create(vals_list)
        # Log initial stage transition for every freshly-created journey.
        for rec in records:
            self.env["onboarding.stage.transition"].sudo().create(
                {
                    "journey_id": rec.id,
                    "from_stage": False,
                    "to_stage": rec.stage,
                    "notes": "Journey created.",
                }
            )
        return records

    def write(self, vals):
        # Capture intended stage changes BEFORE super so we can validate.
        if "stage" in vals:
            new_stage = vals["stage"]
            for rec in self:
                old = rec.stage
                if old == new_stage:
                    continue
                allowed = _FORWARD.get(old, set())
                if new_stage not in allowed and not self.env.context.get("_force_stage"):
                    raise ValidationError(
                        _("Illegal stage transition: %(old)s -> %(new)s")
                        % {
                            "old": old,
                            "new": new_stage,
                        }
                    )
            # Bump sync version on stage move.
            vals["sync_version"] = (
                max((r.sync_version or 0) for r in self) + 1 if self else (vals.get("sync_version") or 1)
            )

        result = super().write(vals)

        if "stage" in vals:
            new_stage = vals["stage"]
            for rec in self:
                # Audit row
                self.env["onboarding.stage.transition"].sudo().create(
                    {
                        "journey_id": rec.id,
                        "from_stage": rec._origin_stage_cache(),
                        "to_stage": new_stage,
                        "notes": vals.get("_transition_notes") or "",
                    }
                )
                rec.message_post(body=_("Stage moved to %s") % new_stage)
                # Auto-archive linked project when journey closes.
                if new_stage == "closed" and rec.project_id and rec.project_id.active:
                    rec.project_id.with_context(_skip_journey_sync=True).write({"active": False})
        return result

    def _origin_stage_cache(self):
        """Return the stage before this transaction wrote. Falls back to current."""
        self.ensure_one()
        # In Odoo 19 ORM the previous value is no longer easily available in
        # write() post-super; we approximate via the latest transition row.
        last = self.env["onboarding.stage.transition"].search(
            [("journey_id", "=", self.id)],
            order="transitioned_at desc, id desc",
            limit=1,
        )
        return last.to_stage if last else False

    # ------------------------------------------------------------------ smart buttons
    def action_open_brds(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("BRD Documents"),
            "res_model": "brd.document",
            "view_mode": "list,form",
            "domain": [("journey_id", "=", self.id)],
            "context": {"default_journey_id": self.id},
        }

    def action_open_recommendations(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Recommendations"),
            "res_model": "brd.recommendation",
            "view_mode": "list,form",
            "domain": [("document_id.journey_id", "=", self.id)],
        }

    def action_open_project(self):
        self.ensure_one()
        if not self.project_id:
            raise UserError(_("No project linked yet."))
        return {
            "type": "ir.actions.act_window",
            "name": self.project_id.display_name,
            "res_model": "project.project",
            "res_id": self.project_id.id,
            "view_mode": "form",
        }

    def action_open_tasks(self):
        self.ensure_one()
        if not self.project_id:
            raise UserError(_("No project linked yet."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Tasks"),
            "res_model": "project.task",
            "view_mode": "kanban,list,form",
            "domain": [("project_id", "=", self.project_id.id)],
        }

    def action_open_tenant(self):
        self.ensure_one()
        if not self.tenant_registry_id:
            raise UserError(_("No tenant linked yet."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "tenant.registry",
            "res_id": self.tenant_registry_id.id,
            "view_mode": "form",
        }

    def action_open_vps(self):
        self.ensure_one()
        if not self.tenant_vps_id:
            raise UserError(_("No VPS linked yet."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "tenant.vps",
            "res_id": self.tenant_vps_id.id,
            "view_mode": "form",
        }

    # ------------------------------------------------------------------ wizard launchers
    def action_launch_brd_upload(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Upload BRD"),
            "res_model": "onboarding.brd.upload.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_journey_id": self.id},
        }

    def action_launch_go_no_go(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Go / No-Go"),
            "res_model": "onboarding.go.no.go.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_journey_id": self.id},
        }

    def action_launch_provision(self):
        """Open the tenant provision wizard, pre-filling the industry pack from
        the intake vertical and identity from the captured profile. Everything
        remains editable — human-in-the-loop is preserved."""
        self.ensure_one()
        profile = {}
        if self.company_profile_json:
            try:
                profile = json.loads(self.company_profile_json) or {}
            except (ValueError, TypeError):
                profile = {}

        ctx = dict(self.env.context)
        display_name = (
            profile.get("company_name") or profile.get("name") or (self.partner_id.name if self.partner_id else "")
        )
        if display_name:
            ctx["default_display_name"] = display_name
        email = profile.get("contact_email") or (self.partner_id.email if self.partner_id else "")
        if email:
            ctx["default_contact_email"] = email
        if profile.get("contact_phone"):
            ctx["default_contact_phone"] = profile["contact_phone"]

        # Industry pack from the intake vertical (hub_console is a transitive
        # dependency via tenant_infra, but guard defensively all the same).
        vertical = profile.get("vertical_target") or profile.get("vertical")
        if vertical and "custom.hub.industry.pack" in self.env:
            pack = self.env["custom.hub.industry.pack"].sudo().search([("code", "=", vertical)], limit=1)
            if pack:
                ctx["default_pack_id"] = pack.id

        return {
            "type": "ir.actions.act_window",
            "name": _("Provision Tenant"),
            "res_model": "tenant.provision.wizard",
            "view_mode": "form",
            "target": "new",
            "context": ctx,
        }
