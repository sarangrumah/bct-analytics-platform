# -*- coding: utf-8 -*-
"""Per-mitra PPS credential: the ``user`` login POS authenticates with, the
password (indirected via ``ir.config_parameter``), the IP allowlist and the
callback URL. Mirrors the credential-indirection convention used by
``custom.ppob.va.bank.connection`` -- the secret is NEVER stored on the record.
"""

from odoo import fields, models


class PpsMitraCredential(models.Model):
    _name = "custom.ppob.pps.mitra.credential"
    _description = "PPS Gateway: Mitra Credential"
    _order = "pps_user"
    _rec_name = "pps_user"

    mitra_id = fields.Many2one(
        comodel_name="res.partner",
        string="Mitra",
        required=True,
        domain=[("x_custom_ppob_is_mitra", "=", True)],
        ondelete="cascade",
    )
    pps_user = fields.Char(
        string="PPS User",
        required=True,
        index=True,
        copy=False,
        help="The 'user' login string POS sends on every PPS request.",
    )
    credential_ref = fields.Char(
        string="Password Key (ir.config_parameter)",
        required=True,
        help="Name of the ir.config_parameter holding this mitra's PPS "
        "password/shared-secret. NEVER paste the password here.",
    )
    callback_url = fields.Char(
        help="Partner callback URL. Odoo fires the PPS GET callback here on "
        "terminal status. Leave empty to disable push (POS polls StatusTrx).",
    )
    ip_whitelist = fields.Char(string="IP Allowlist (CIDR, comma-separated)")
    max_clock_skew_s = fields.Integer(string="Max Clock Skew (s)", default=300)
    status = fields.Selection(
        selection=[("active", "Active"), ("disabled", "Disabled")],
        default="active",
        required=True,
        index=True,
    )

    _pps_user_uniq = models.Constraint("unique(pps_user)", "PPS user login must be unique.")

    def _get_password(self):
        self.ensure_one()
        return self.env["ir.config_parameter"].sudo().get_param(self.credential_ref or "", "")
