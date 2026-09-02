# -*- coding: utf-8 -*-
"""Brand vertical -- the brand axis every piece of work hangs off."""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CustomProjectVertical(models.Model):
    _name = "custom.project.vertical"
    _description = "VAS Brand Vertical"
    _inherit = ["pdp.audited.mixin"]
    _order = "sequence, code"

    name = fields.Char(required=True, help="Brand name as people actually say it.")
    code = fields.Char(
        required=True,
        help="Short uppercase key (RETAIL, POS, ...). Used in badges, message templates and record prefixes.",
    )
    legal_entity = fields.Char(
        string="Legal Entity",
        help="Registered company behind the brand. Deliberately blank when unconfirmed "
        "-- an empty cell is honest, a guessed one is not.",
    )
    brand_group = fields.Selection(
        [
            ("retail_fashion", "Retail Fashion"),
            ("retail_gadget", "Retail Gadget"),
            ("digital", "Digital"),
            ("supply_chain", "Supply Chain"),
            ("shared", "Shared Services"),
        ],
        default="digital",
        required=True,
    )
    vertical_po_id = fields.Many2one(
        "res.users",
        string="Vertical PO",
        help="Product Owner accountable for this brand.",
    )
    ba_ids = fields.Many2many(
        "res.users",
        "custom_vertical_ba_rel",
        "vertical_id",
        "user_id",
        string="Business Analysts",
    )
    pic_partner_ids = fields.Many2many(
        "res.partner",
        "custom_vertical_pic_rel",
        "vertical_id",
        "partner_id",
        string="Brand PIC",
        help="Brand-side contacts. These are the people notified when work reaches Waiting User Verification.",
    )
    color = fields.Integer(default=0, help="Kanban / badge colour index.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    project_ids = fields.One2many("project.project", "custom_vertical_id", string="Projects")
    project_count = fields.Integer(compute="_compute_counts")
    task_count = fields.Integer(compute="_compute_counts")

    _code_uniq = models.Constraint(
        "unique(code)",
        "A vertical with this code already exists.",
    )

    @api.depends("project_ids")
    def _compute_counts(self):
        project_data = self.env["project.project"]._read_group(
            [("custom_vertical_id", "in", self.ids)],
            groupby=["custom_vertical_id"],
            aggregates=["__count"],
        )
        projects = {v.id: c for v, c in project_data}
        task_data = self.env["project.task"]._read_group(
            [("custom_vertical_id", "in", self.ids)],
            groupby=["custom_vertical_id"],
            aggregates=["__count"],
        )
        tasks = {v.id: c for v, c in task_data}
        for rec in self:
            rec.project_count = projects.get(rec.id, 0)
            rec.task_count = tasks.get(rec.id, 0)

    @api.constrains("code")
    def _check_code(self):
        for rec in self:
            if rec.code and (" " in rec.code or rec.code != rec.code.upper()):
                raise ValidationError(_("Vertical code must be uppercase without spaces: %s", rec.code))

    @api.depends("code", "name")
    def _compute_display_name(self):
        # Odoo 17+ replaced name_get() with this compute.
        for rec in self:
            rec.display_name = f"{rec.code} — {rec.name}" if rec.code else rec.name

    def label_for_message(self):
        """Vertical line used in WhatsApp / e-mail templates."""
        self.ensure_one()
        if self.legal_entity:
            return f"{self.name} ({self.legal_entity})"
        return self.name
