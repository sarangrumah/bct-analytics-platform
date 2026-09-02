# -*- coding: utf-8 -*-
"""Immutable snapshot of an article body, created on each body change.

Restoring a version writes the snapshot back to the parent article, which
in turn produces *another* snapshot — so the restore itself is reversible.
"""

from odoo import _, fields, models
from odoo.exceptions import AccessError


class KnowledgeArticleVersion(models.Model):
    _name = "knowledge.article.version"
    _description = "Knowledge Article Version Snapshot"
    _order = "article_id, version_no desc"

    article_id = fields.Many2one(
        "knowledge.article",
        string="Article",
        required=True,
        ondelete="cascade",
        index=True,
    )
    version_no = fields.Integer(required=True)
    body_snapshot = fields.Html(sanitize=False, readonly=True)
    saved_by = fields.Many2one(
        "res.users",
        string="Saved By",
        default=lambda self: self.env.user,
        readonly=True,
    )
    saved_at = fields.Datetime(
        string="Saved At",
        default=fields.Datetime.now,
        readonly=True,
    )

    def action_restore_version(self):
        """Write this snapshot back to the parent article."""
        self.ensure_one()
        # Permission: must be able to write the parent.
        try:
            self.article_id.check_access("write")
        except AccessError:
            raise
        self.article_id.write({"body": self.body_snapshot or ""})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Version restored"),
                "message": _("Version %s has been restored.") % self.version_no,
                "type": "success",
            },
        }
