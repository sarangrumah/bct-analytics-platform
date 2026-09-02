# -*- coding: utf-8 -*-
"""Portfolio -- a group of projects with one accountable owner."""

from odoo import api, fields, models


class CustomProjectPortfolio(models.Model):
    _name = "custom.project.portfolio"
    _description = "VAS Portfolio"
    _inherit = ["pdp.audited.mixin"]
    _order = "sequence, name"

    name = fields.Char(required=True)
    code = fields.Char(required=True)
    owner_id = fields.Many2one("res.users", string="Portfolio Owner", required=True)
    objective = fields.Text(help="What this portfolio is for, in one paragraph.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    project_ids = fields.One2many("project.project", "custom_portfolio_id", string="Projects")
    health = fields.Selection(
        [
            ("on_track", "On track"),
            ("at_risk", "At risk"),
            ("blocked", "Blocked"),
        ],
        compute="_compute_health",
        store=True,
        help="Worst health among the portfolio's projects -- a portfolio is only as "
        "healthy as its most troubled project.",
    )

    _code_uniq = models.Constraint(
        "unique(code)",
        "A portfolio with this code already exists.",
    )

    @api.depends("project_ids.custom_health")
    def _compute_health(self):
        rank = {"on_track": 0, "at_risk": 1, "blocked": 2}
        inverse = {v: k for k, v in rank.items()}
        for rec in self:
            worst = 0
            for project in rec.project_ids:
                worst = max(worst, rank.get(project.custom_health, 0))
            rec.health = inverse[worst]
