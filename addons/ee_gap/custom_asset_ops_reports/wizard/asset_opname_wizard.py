# -*- coding: utf-8 -*-
from odoo import fields, models


class AssetOpnameWizard(models.TransientModel):
    _name = "custom.report.asset.opname.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "Asset Opname Report Wizard"
    _report_code = "asset_opname"

    company_ids = fields.Many2many("res.company", default=lambda self: self.env.companies)
    # Explicit relation names: the auto-generated ones
    # ("custom_fixed_asset_location_custom_report_asset_opname_wizard_rel")
    # exceed PostgreSQL's 63-character identifier limit.
    group_ids = fields.Many2many(
        "custom.fixed.asset.group",
        relation="opname_wizard_asset_group_rel",
        column1="wizard_id",
        column2="group_id",
        string="Groups",
    )
    location_ids = fields.Many2many(
        "custom.fixed.asset.location",
        relation="opname_wizard_asset_location_rel",
        column1="wizard_id",
        column2="location_id",
        string="Locations",
    )
    state = fields.Selection(
        [("draft", "Draft"), ("running", "Running"), ("disposed", "Disposed"), ("cancelled", "Cancelled")],
        string="Asset State",
    )

    def _build_filters(self):
        self.ensure_one()
        return {
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "group_ids": self.group_ids.ids,
            "location_ids": self.location_ids.ids,
            "state": self.state,
        }

    def action_export_xlsx(self):
        self.ensure_one()
        return self.env["custom.report.asset.opname"]._xlsx_action(self._build_filters(), "Asset_Opname.xlsx")
