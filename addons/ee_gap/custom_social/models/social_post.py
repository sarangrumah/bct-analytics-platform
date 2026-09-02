# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


POST_STATES = [
    ("draft", "Draft"),
    ("scheduled", "Scheduled"),
    ("published", "Published"),
    ("failed", "Failed"),
    ("cancelled", "Cancelled"),
]


class SocialPost(models.Model):
    _name = "social.post"
    _description = "Social Post"
    _inherit = ["mail.thread", "pdp.audited.mixin"]
    _order = "scheduled_at desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    account_id = fields.Many2one("social.account", required=True, index=True)
    body = fields.Text(required=True, tracking=True)
    media_attachment_id = fields.Many2one("ir.attachment", ondelete="set null")
    scheduled_at = fields.Datetime(required=True, tracking=True)
    published_at = fields.Datetime(readonly=True)
    external_post_id = fields.Char(readonly=True, copy=False, help="Platform-issued ID of the published post.")
    state = fields.Selection(POST_STATES, default="draft", required=True, tracking=True, index=True)
    last_error = fields.Text(readonly=True)

    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    @api.depends("account_id", "scheduled_at")
    def _compute_name(self):
        for rec in self:
            who = rec.account_id.handle if rec.account_id else "?"
            rec.name = f"{who} @ {rec.scheduled_at or 'unscheduled'}"

    def _pdp_audit_classification(self):
        return "public"

    def action_schedule(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only draft posts can be scheduled."))
            rec.write({"state": "scheduled"})
            rec._pdp_audit_write("social_post_scheduled", rec.id, None)

    def action_publish_now(self):
        for rec in self:
            rec._publish()

    def action_cancel(self):
        for rec in self:
            if rec.state == "published":
                raise UserError(_("Cannot cancel a published post."))
            rec.write({"state": "cancelled"})
            rec._pdp_audit_write("social_post_cancel", rec.id, None)

    def _publish(self):
        """Execute the publish via the per-platform adapter (stub here)."""
        self.ensure_one()
        try:
            # Real implementation would dispatch to a platform-specific adapter
            external_id = f"manual-{fields.Datetime.now().isoformat()}"
            self.write(
                {
                    "state": "published",
                    "published_at": fields.Datetime.now(),
                    "external_post_id": external_id,
                    "last_error": False,
                }
            )
            self._pdp_audit_write(
                "social_post_published", self.id, {"platform": self.account_id.platform, "external_id": external_id}
            )
        except Exception as e:
            _logger.exception("Social publish failed for post %s", self.id)
            self.write({"state": "failed", "last_error": str(e)})

    @api.model
    def _cron_publish_due(self):
        due = self.sudo().search(
            [
                ("state", "=", "scheduled"),
                ("scheduled_at", "<=", fields.Datetime.now()),
            ]
        )
        for post in due:
            try:
                post._publish()
            except Exception:
                _logger.exception("Cron publish failed for post %s", post.id)
