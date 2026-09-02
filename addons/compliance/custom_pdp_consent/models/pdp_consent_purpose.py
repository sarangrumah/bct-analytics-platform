# -*- coding: utf-8 -*-
from odoo import fields, models


class PdpConsentPurpose(models.Model):
    _name = "pdp.consent.purpose"
    _description = "PDP Consent Purpose"
    _order = "sequence, code"
    _rec_name = "name"

    sequence = fields.Integer(default=10)
    code = fields.Char(required=True, index=True)
    name = fields.Char(required=True, translate=True)
    description = fields.Text(translate=True)
    requires_renewal_days = fields.Integer(
        default=0,
        help="If > 0, recorded consents expire and require renewal after N days.",
    )
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint(
        "unique(code)",
        "Consent purpose code must be unique.",
    )
