# -*- coding: utf-8 -*-
"""Notification rules -- the status-to-recipient map, as data instead of code."""

from odoo import api, fields, models

NOTIFY_EVENTS = [
    ("task_created", "Task created"),
    ("assigned", "Task assigned"),
    ("stage_changed", "Stage changed"),
    ("task_closed", "Task closed"),
    ("on_hold", "Put on hold"),
    ("resumed", "Resumed from hold"),
    ("hold_expired", "Hold outlived its estimate"),
    ("verify_request", "User verification requested"),
    ("verify_reminder_h2", "Verification reminder (H+2)"),
    ("verify_reminder_h5", "Verification reminder (H+5)"),
    ("verify_auto_close", "Auto-closed after silence"),
    ("due_h3", "Due in 3 days"),
    ("due_h1", "Due tomorrow"),
    ("overdue", "Overdue"),
    ("escalation", "Escalation"),
    ("health_degraded", "Project health degraded"),
    ("cr_created", "Change request created"),
    ("cr_analysis", "Change request taken into analysis"),
    ("cr_submit", "Change request submitted for approval"),
    ("cr_approve", "Change request approved"),
    ("cr_reject", "Change request rejected"),
    ("cr_closed", "Change request closed"),
    ("cr_intake_overdue", "Intake past its response SLA"),
    ("weekly_reminder", "Weekly report reminder"),
    ("weekly_submitted", "Weekly report submitted"),
    ("weekly_digest", "Weekly digest"),
]

RECIPIENT_KINDS = [
    ("assignee", "Assignee"),
    ("reporter", "Reporter / requester"),
    ("ba", "Business Analyst"),
    ("po", "Product Owner"),
    ("portfolio_owner", "Portfolio owner"),
    ("vertical_owner", "Vertical PO"),
    ("brand_pic", "Brand PIC (outside the team)"),
    ("group", "Everyone in a group"),
]


class CustomProjectNotifyRule(models.Model):
    _name = "custom.project.notify.rule"
    _description = "VAS Notification Rule"
    _inherit = ["pdp.audited.mixin"]
    _order = "event, sequence"

    name = fields.Char(compute="_compute_name", store=True)
    event = fields.Selection(NOTIFY_EVENTS, required=True, index=True)
    recipient_kind = fields.Selection(RECIPIENT_KINDS, required=True)
    role_group_id = fields.Many2one(
        "res.groups",
        string="Group",
        help="Only used when the recipient kind is 'Everyone in a group'.",
    )
    channel_wa = fields.Boolean(string="WhatsApp", default=True)
    channel_email = fields.Boolean(string="E-mail", default=True)
    channel_odoo = fields.Boolean(string="Odoo inbox", default=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _rule_uniq = models.Constraint(
        "unique(event, recipient_kind, role_group_id)",
        "That event already has a rule for this recipient kind.",
    )

    @api.depends("event", "recipient_kind")
    def _compute_name(self):
        events = dict(NOTIFY_EVENTS)
        kinds = dict(RECIPIENT_KINDS)
        for rule in self:
            rule.name = "%s → %s" % (
                events.get(rule.event, rule.event or "?"),
                kinds.get(rule.recipient_kind, rule.recipient_kind or "?"),
            )

    @api.model
    def rules_for(self, event):
        return self.search([("event", "=", event)])
