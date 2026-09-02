# -*- coding: utf-8 -*-
"""Mark a company as part of an intercompany group + helpers for partner linkage."""

from __future__ import annotations

from odoo import api, fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    x_custom_ic_enabled = fields.Boolean(
        string="Intercompany Mirror Enabled",
        default=True,
        help="Globally enable / disable automatic intercompany mirroring for this "
        "company. Useful as a kill-switch during migration.",
    )

    @api.model
    def _sister_companies(self):
        """Return companies in the same intercompany perimeter as ``self.env.company``."""
        if not self.env.company.x_custom_ic_enabled:
            return self.browse()
        # All companies where there's a rule from/to current company
        Rule = self.env["account.intercompany.rule"].sudo()
        rules = Rule.search(
            [
                ("active", "=", True),
                "|",
                ("company_from_id", "=", self.env.company.id),
                ("company_to_id", "=", self.env.company.id),
            ]
        )
        return (rules.mapped("company_from_id") | rules.mapped("company_to_id")) - self.env.company
