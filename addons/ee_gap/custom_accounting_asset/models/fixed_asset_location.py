# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class CustomFixedAssetLocation(models.Model):
    _name = "custom.fixed.asset.location"
    _description = "Custom Fixed Asset Location"
    _order = "complete_name"
    _parent_store = True
    _parent_name = "parent_id"

    name = fields.Char(required=True)
    code = fields.Char()
    complete_name = fields.Char(
        compute="_compute_complete_name",
        recursive=True,
        store=True,
    )
    parent_id = fields.Many2one(
        comodel_name="custom.fixed.asset.location",
        string="Parent Location",
        ondelete="restrict",
        index=True,
    )
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many(
        comodel_name="custom.fixed.asset.location",
        inverse_name="parent_id",
        string="Sub-locations",
    )
    address = fields.Text()
    note = fields.Char()
    active = fields.Boolean(default=True)

    @api.depends("name", "parent_id.complete_name")
    def _compute_complete_name(self):
        for loc in self:
            if loc.parent_id:
                loc.complete_name = f"{loc.parent_id.complete_name} / {loc.name}"
            else:
                loc.complete_name = loc.name

    @api.constrains("parent_id")
    def _check_location_recursion(self):
        if not self._check_recursion():
            raise ValidationError(self.env._("You cannot create recursive asset locations."))
