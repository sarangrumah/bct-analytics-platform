# -*- coding: utf-8 -*-
"""Add the ``spot_check`` sampling method to cycle-count plans.

A spot check is an ad-hoc, small random sample; the actual sampling lives
in the start-wizard override (see ``wizard/cycle_count_start_wizard.py``).
"""

from odoo import fields, models


class CycleCountPlan(models.Model):
    _inherit = "custom.cycle.count.plan"

    method = fields.Selection(
        selection_add=[("spot_check", "Spot Check")],
        ondelete={"spot_check": "set default"},
    )
