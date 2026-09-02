# -*- coding: utf-8 -*-
from odoo import fields, models

from .ppob_va_account import BANK_CODES


class PpobVaBankConnection(models.Model):
    _name = "custom.ppob.va.bank.connection"
    _description = "VA H2H Connection Config"

    name = fields.Char(required=True)
    bank_code = fields.Selection(selection=BANK_CODES, required=True)
    endpoint_inbound = fields.Char(
        help="Fully qualified URL exposed by this Odoo for the bank to call on "
        "inquiry/payment. Informational - the actual route is fixed by the "
        "controller at /api/ppob/va/<bank>/*.",
    )
    credential_ref = fields.Char(
        help="ir.config_parameter key holding the HMAC secret shared with the bank.",
    )
    ip_whitelist = fields.Char(
        help="Comma-separated list of IP addresses / CIDRs allowed to hit the "
        "callback endpoints. Behind a reverse proxy, the first "
        "X-Forwarded-For hop is checked.",
    )
    status = fields.Selection(
        selection=[
            ("active", "Active"),
            ("inactive", "Inactive"),
        ],
        default="inactive",
        required=True,
    )
    max_clock_skew_s = fields.Integer(
        default=300,
        help="Reject callbacks whose X-Timestamp is older than this.",
    )

    _bank_uniq = models.Constraint(
        "unique(bank_code)",
        "One connection row per bank.",
    )
