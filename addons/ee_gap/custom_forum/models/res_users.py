# -*- coding: utf-8 -*-
"""Indonesian community-grade reputation badge on res.users.

CE ``website_forum`` exposes karma directly on ``res.users``; we add a
derived ``x_indonesia_badge`` so dashboards can group members by tier in
Bahasa Indonesia.
"""

from odoo import api, fields, models


# Karma thresholds chosen to roughly mirror Odoo's stock forum karma
# milestones (post answer / comment / edit / moderate).
_BADGE_THRESHOLDS = (
    ("master", 5000),
    ("ahli", 1000),
    ("lanjut", 200),
    ("pemula", 0),
)


class ResUsers(models.Model):
    _inherit = "res.users"

    x_indonesia_badge = fields.Selection(
        selection=[
            ("pemula", "Pemula"),
            ("lanjut", "Lanjut"),
            ("ahli", "Ahli"),
            ("master", "Master"),
        ],
        string="Forum Badge (ID)",
        compute="_compute_x_indonesia_badge",
        store=True,
        help="Tier derived from karma: pemula 0+, lanjut 200+, ahli 1000+, master 5000+.",
    )

    @api.depends("karma")
    def _compute_x_indonesia_badge(self):
        for user in self:
            karma = user.karma or 0
            badge = "pemula"
            for tier, threshold in _BADGE_THRESHOLDS:
                if karma >= threshold:
                    badge = tier
                    break
            user.x_indonesia_badge = badge
