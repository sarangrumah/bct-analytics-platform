# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields, models


class AssetRegisterWizard(models.TransientModel):
    _name = "custom.report.asset.register.wizard"
    _description = "Asset Register Wizard"

    year = fields.Integer(
        required=True,
        default=lambda self: fields.Date.context_today(self).year,
    )
    company_ids = fields.Many2many(
        "res.company",
        default=lambda self: self.env.companies,
    )
    # Explicit short relation names: the auto-generated names
    # (custom_fixed_asset_group_custom_report_asset_register_wizard_rel, etc.)
    # exceed PostgreSQL's 63-char identifier limit and break registry load on Odoo 19.
    group_ids = fields.Many2many(
        "custom.fixed.asset.group",
        relation="asset_reg_wiz_group_rel",
        string="Groups",
    )
    location_ids = fields.Many2many(
        "custom.fixed.asset.location",
        relation="asset_reg_wiz_location_rel",
        string="Locations",
    )
    asset_states = fields.Selection(
        [
            ("running", "Running only"),
            ("all", "All (incl. draft/disposed)"),
        ],
        string="Assets",
        default="running",
        required=True,
    )

    def _build_filters(self):
        self.ensure_one()
        states = ["running"] if self.asset_states == "running" else []
        return {
            "date_from": date(self.year, 1, 1),
            "date_to": date(self.year, 12, 31),
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "group_ids": self.group_ids.ids,
            "location_ids": self.location_ids.ids,
            "asset_states": states,
            "year": self.year,
        }

    def _options(self):
        filters = self._build_filters()
        return {
            **filters,
            "date_from": filters["date_from"].isoformat(),
            "date_to": filters["date_to"].isoformat(),
        }

    def action_print(self):
        self.ensure_one()
        data = {
            "doc_model": self._name,
            "options": self._options(),
        }
        return self.env.ref("custom_accounting_asset.action_report_asset_register").report_action(self, data=data)

    def action_export_xlsx(self):
        self.ensure_one()
        filename = "Asset_Register_%s.xlsx" % self.year
        return self.env["custom.report.asset.register"]._xlsx_action(self._options(), filename)
