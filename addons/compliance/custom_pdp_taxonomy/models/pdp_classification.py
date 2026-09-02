# -*- coding: utf-8 -*-
"""PDP data classification taxonomy."""

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class PdpClassification(models.Model):
    _name = "pdp.classification"
    _description = "PDP Data Classification"
    _order = "sequence, code"
    _rec_name = "code"

    sequence = fields.Integer(default=10)
    code = fields.Char(required=True, index=True)
    name = fields.Char(required=True, translate=True)
    description = fields.Text(translate=True)
    requires_consent = fields.Boolean(
        default=False,
        help="Processing this classification requires recorded subject consent.",
    )
    requires_masking = fields.Boolean(
        default=False,
        help="Field values of this classification must be masked in UI/export by default.",
    )
    default_retention_days = fields.Integer(
        default=0,
        help="Default retention period (days). 0 = unlimited / governed elsewhere.",
    )
    color = fields.Integer(default=0)
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint(
        "unique(code)",
        "Classification code must be unique.",
    )

    @api.constrains("code")
    def _check_code(self):
        for rec in self:
            if not rec.code or " " in rec.code:
                raise ValidationError("Classification code must be non-empty and contain no spaces.")

    @api.model
    def _seed_partner_pii_fields(self):
        """Tag default PII-classified fields on res.partner.

        Called from data XML <function/>; idempotent.
        """
        mapping = {
            "name": "pii",
            "phone": "pii",
            "mobile": "pii",
            "email": "pii",
            "vat": "financial",
        }
        # Odoo 19 blocks ORM writes to base ir.model.fields rows.
        # Use raw SQL to set the cross-cutting PDP tag column we added via _inherit.
        for fname, code in mapping.items():
            classif = self.search([("code", "=", code)], limit=1)
            if not classif:
                continue
            self.env.cr.execute(
                """
                UPDATE ir_model_fields
                   SET x_pdp_classification_id = %s
                 WHERE model = %s
                   AND name = %s
                   AND (x_pdp_classification_id IS NULL OR x_pdp_classification_id = 0)
                """,
                (classif.id, "res.partner", fname),
            )
        return True
