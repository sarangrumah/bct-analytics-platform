# -*- coding: utf-8 -*-
"""Wizard: trigger ``custom.hub.module.catalog._action_scan_all()``."""

from __future__ import annotations

from odoo import _, fields, models


class CustomHubRescanCatalogWizard(models.TransientModel):
    _name = "custom.hub.rescan.catalog.wizard"
    _description = "Rescan Module Catalog Wizard"

    result_summary = fields.Char(readonly=True)

    def action_run(self):
        self.ensure_one()
        out = self.env["custom.hub.module.catalog"].sudo()._action_scan_all()
        self.result_summary = _("Scan complete: created=%(created)s updated=%(updated)s total=%(total)s") % out
        return {
            "type": "ir.actions.act_window",
            "name": _("Module Catalog"),
            "res_model": "custom.hub.module.catalog",
            "view_mode": "list,form,kanban",
            "target": "current",
        }
