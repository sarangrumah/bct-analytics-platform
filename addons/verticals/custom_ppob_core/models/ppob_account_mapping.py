# -*- coding: utf-8 -*-
from odoo import api, fields, models


class PpobAccountMapping(models.Model):
    """Role-addressed GL account resolver for the PPOB suite.

    Downstream modules resolve accounts by a stable ``role`` key
    (e.g. ``ppn_masukan``, ``provider_deposit_default``) rather than by
    xmlid, so the post-init scaffolding can bind to a tenant's existing
    chart without every consumer knowing the concrete account.
    """

    _name = "custom.ppob.account.mapping"
    _description = "PPOB Account Role Mapping"
    _order = "company_id, role"

    company_id = fields.Many2one(
        comodel_name="res.company",
        required=True,
        ondelete="cascade",
        default=lambda self: self.env.company,
        index=True,
    )
    role = fields.Char(
        required=True,
        index=True,
        help="Stable role key referenced by PPOB business logic "
        "(e.g. wallet_liab_telko, ppn_masukan, provider_deposit_default).",
    )
    account_id = fields.Many2one(
        comodel_name="account.account",
        string="GL Account",
        required=True,
        ondelete="restrict",
    )

    _company_role_uniq = models.Constraint(
        "unique(company_id, role)",
        "Each account role can be mapped only once per company.",
    )

    @api.model
    def _get_account(self, role, company=None):
        """Return the ``account.account`` mapped to ``role`` for ``company``.

        Returns an empty recordset if unmapped (callers decide whether that
        is an error in their context)."""
        company = company or self.env.company
        rec = self.search([("company_id", "=", company.id), ("role", "=", role)], limit=1)
        return rec.account_id
