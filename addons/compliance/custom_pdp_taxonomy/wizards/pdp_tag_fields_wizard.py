# -*- coding: utf-8 -*-
"""Wizard for batch tagging ir.model.fields with PDP classifications."""

from odoo import api, fields, models
from odoo.exceptions import UserError


class PdpTagFieldsWizard(models.TransientModel):
    _name = "pdp.tag.fields.wizard"
    _description = "Batch Tag PII Fields"

    model_id = fields.Many2one("ir.model", string="Model", required=True)
    field_ids = fields.Many2many(
        "ir.model.fields",
        string="Fields",
        domain="[('model_id', '=', model_id)]",
    )
    classification_id = fields.Many2one(
        "pdp.classification",
        string="Classification",
        required=True,
    )

    @api.onchange("model_id")
    def _onchange_model(self):
        self.field_ids = [(5, 0, 0)]

    def action_apply(self):
        self.ensure_one()
        if not self.field_ids:
            raise UserError("Select at least one field.")
        self.field_ids.write({"x_pdp_classification_id": self.classification_id.id})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "PDP Tagging",
                "message": "Tagged %d fields as %s"
                % (
                    len(self.field_ids),
                    self.classification_id.code,
                ),
                "type": "success",
                "sticky": False,
            },
        }
