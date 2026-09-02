# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class DocumentVersion(models.Model):
    _name = "document.version"
    _description = "Document Version (Immutable)"
    _order = "document_id, version desc"

    document_id = fields.Many2one("document.document", required=True, ondelete="cascade", index=True)
    attachment_id = fields.Many2one("ir.attachment", required=True, ondelete="restrict")
    version = fields.Integer(required=True)
    comment = fields.Char()
    uploaded_by_id = fields.Many2one("res.users", default=lambda s: s.env.user, required=True)
    uploaded_at = fields.Datetime(default=fields.Datetime.now, required=True)

    _uniq_document_version = models.Constraint(
        "unique(document_id, version)",
        "Version number must be unique per document.",
    )

    def write(self, vals):
        if self.env.context.get("document_version_internal"):
            return super().write(vals)
        raise UserError(_("Document versions are immutable."))

    def unlink(self):
        raise UserError(_("Document versions cannot be deleted."))
