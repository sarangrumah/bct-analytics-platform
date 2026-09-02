# -*- coding: utf-8 -*-
"""Declare ``affects_existing_module_ids`` on ``brd.recommendation``.

This M2M targets ``custom.hub.module.catalog`` which lives in
``custom_hub_console``. brd_analyzer can't declare it directly because
hub_console depends on brd_analyzer (would create a cycle). Declaring it
here is safe: ``custom_onboarding_journey`` already depends on
``custom_tenant_infra`` which depends on ``custom_hub_console``.
"""

from odoo import fields, models


class BrdRecommendationHubLink(models.Model):
    _inherit = "brd.recommendation"

    affects_existing_module_ids = fields.Many2many(
        comodel_name="custom.hub.module.catalog",
        relation="brd_recommendation_hub_catalog_rel",
        column1="recommendation_id",
        column2="catalog_id",
        string="Affects Existing Hub Modules",
        help="Hub catalog modules this recommendation would extend, patch or affect.",
    )
