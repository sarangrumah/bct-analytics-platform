# -*- coding: utf-8 -*-
import secrets

from odoo import api, fields, models


class Document(models.Model):
    _name = "document.document"
    _description = "Document"
    _inherit = ["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]
    _order = "create_date desc"

    name = fields.Char(required=True)
    workspace_id = fields.Many2one("document.workspace", required=True, index=True)
    tag_ids = fields.Many2many(
        "document.tag",
        "document_tag_rel",
        "doc_id",
        "tag_id",
        string="Tags",
    )
    classification_id = fields.Many2one(
        "pdp.classification",
        compute="_compute_classification",
        store=True,
        readonly=False,
    )

    attachment_id = fields.Many2one(
        "ir.attachment",
        required=True,
        ondelete="cascade",
        copy=False,
    )
    filename = fields.Char(related="attachment_id.name", readonly=True)
    mimetype = fields.Char(related="attachment_id.mimetype", readonly=True)
    file_size = fields.Integer(related="attachment_id.file_size", readonly=True)

    description = fields.Text()
    version_count = fields.Integer(compute="_compute_version_count")
    share_token = fields.Char(readonly=True, copy=False)
    share_expires_at = fields.Datetime()

    owner_id = fields.Many2one("res.users", default=lambda s: s.env.user, required=True)
    state = fields.Selection(
        [("draft", "Draft"), ("published", "Published"), ("archived", "Archived")],
        default="draft",
        required=True,
        tracking=True,
    )

    @api.depends("workspace_id")
    def _compute_classification(self):
        for rec in self:
            if not rec.classification_id and rec.workspace_id:
                rec.classification_id = rec.workspace_id.default_classification_id

    def _compute_version_count(self):
        V = self.env["document.version"].sudo()
        for rec in self:
            rec.version_count = V.search_count([("document_id", "=", rec.id)])

    def _pdp_audit_classification(self):
        self.ensure_one()
        return (self.classification_id.code or "internal") if self.classification_id else "internal"

    # ----- Lifecycle -----

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        Version = self.env["document.version"].sudo()
        for rec in records:
            Version.create(
                {
                    "document_id": rec.id,
                    "attachment_id": rec.attachment_id.id,
                    "comment": "Initial version",
                    "version": 1,
                }
            )
        return records

    def action_publish(self):
        for rec in self:
            rec.write({"state": "published"})
            rec._pdp_audit_write("document_publish", rec.id, None)

    def action_archive(self):
        for rec in self:
            rec.write({"state": "archived"})
            rec._pdp_audit_write("document_archive", rec.id, None)

    def action_generate_share_link(self):
        from datetime import timedelta

        for rec in self:
            rec.write(
                {
                    "share_token": secrets.token_urlsafe(32),
                    "share_expires_at": fields.Datetime.now() + timedelta(days=7),
                }
            )
            rec._pdp_audit_write("document_share_link_generated", rec.id, None)
        return True

    def action_revoke_share(self):
        for rec in self:
            rec.write({"share_token": False, "share_expires_at": False})
            rec._pdp_audit_write("document_share_revoked", rec.id, None)

    def action_download(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{self.attachment_id.id}?download=true",
            "target": "self",
        }

    def action_upload_new_version(self, attachment_id: int, comment: str = ""):
        """Called after the user uploads a replacement file."""
        self.ensure_one()
        latest = (
            self.env["document.version"]
            .sudo()
            .search(
                [("document_id", "=", self.id)],
                order="version desc",
                limit=1,
            )
        )
        new_version = (latest.version + 1) if latest else 1
        self.env["document.version"].sudo().create(
            {
                "document_id": self.id,
                "attachment_id": attachment_id,
                "version": new_version,
                "comment": comment,
            }
        )
        self.write({"attachment_id": attachment_id})
        self._pdp_audit_write("document_new_version", self.id, {"version": new_version})
