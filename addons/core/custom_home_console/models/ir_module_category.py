# -*- coding: utf-8 -*-
from odoo import api, fields, models


HOME_CONSOLE_GROUPS = [
    ("work", "Work"),
    ("finance", "Finance"),
    ("hr", "People"),
    ("commerce", "Commerce"),
    ("admin", "Administration"),
    ("insight", "Insight"),
    ("custom", "Custom"),
    ("other", "Other"),
]


# Heuristic mapping from category display name keywords → group.
# Kept intentionally short: anything unmatched falls into "other"
# which the UI groups under a single "Other" bucket.
_KEYWORD_MAP = [
    ("finance", "finance"),
    ("account", "finance"),
    ("invoic", "finance"),
    ("tax", "finance"),
    ("payroll", "hr"),
    ("hr", "hr"),
    ("human", "hr"),
    ("employee", "hr"),
    ("recruit", "hr"),
    ("sale", "commerce"),
    ("purchase", "commerce"),
    ("inventory", "commerce"),
    ("stock", "commerce"),
    ("manufactur", "commerce"),
    ("point of sale", "commerce"),
    ("pos", "commerce"),
    ("ecommerce", "commerce"),
    ("website", "commerce"),
    ("project", "work"),
    ("task", "work"),
    ("planning", "work"),
    ("calendar", "work"),
    ("note", "work"),
    ("discuss", "work"),
    ("knowledge", "work"),
    ("crm", "commerce"),
    ("marketing", "commerce"),
    ("helpdesk", "work"),
    ("dashboard", "insight"),
    ("report", "insight"),
    ("analytic", "insight"),
    ("studio", "admin"),
    ("setting", "admin"),
    ("admin", "admin"),
    ("hub", "admin"),
    ("custom", "custom"),
]


class IrModuleCategory(models.Model):
    _inherit = "ir.module.category"

    home_console_group = fields.Selection(
        selection=HOME_CONSOLE_GROUPS,
        string="Home Console Group",
        compute="_compute_home_console_group",
        store=True,
        readonly=False,
        help="Bucket used by the Home Console to group app cards. "
        "Auto-derived from the category name; admins can override.",
    )

    @api.depends("name")
    def _compute_home_console_group(self):
        for category in self:
            if category.home_console_group:
                # Respect manual overrides (readonly=False + store=True).
                continue
            name = (category.name or "").lower()
            matched = "other"
            for needle, group in _KEYWORD_MAP:
                if needle in name:
                    matched = group
                    break
            category.home_console_group = matched
