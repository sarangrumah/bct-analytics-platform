# -*- coding: utf-8 -*-
from odoo import fields, models


class DocumentWorkspace(models.Model):
    _name = "document.workspace"
    _description = "Document Workspace"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True)
    sequence = fields.Integer(default=10)
    parent_id = fields.Many2one("document.workspace", ondelete="restrict")
    description = fields.Text()
    member_ids = fields.Many2many(
        "res.users",
        "document_workspace_user_rel",
        "workspace_id",
        "user_id",
        string="Members",
    )
    default_classification_id = fields.Many2one(
        "pdp.classification",
        help="Default PDP classification assigned to every document uploaded here.",
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    _code_uniq = models.Constraint(
        "unique(code)",
        "Workspace code must be unique.",
    )
