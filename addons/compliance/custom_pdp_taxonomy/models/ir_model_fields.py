# -*- coding: utf-8 -*-
"""Extend ir.model.fields with a PDP classification reference."""

from odoo import fields, models


class IrModelFields(models.Model):
    _inherit = "ir.model.fields"

    x_pdp_classification_id = fields.Many2one(
        "pdp.classification",
        string="PDP Classification",
        ondelete="set null",
        help="If set, values of this field are governed by the chosen PDP classification (audit, masking, retention).",
    )
