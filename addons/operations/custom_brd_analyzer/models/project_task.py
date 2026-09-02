# -*- coding: utf-8 -*-
"""Add a back-pointer from project.task to the BRD recommendation."""

from __future__ import annotations

from odoo import fields, models


class ProjectTask(models.Model):
    _inherit = "project.task"

    brd_recommendation_id = fields.Many2one(
        "brd.recommendation",
        string="BRD Recommendation",
        ondelete="set null",
        index=True,
        copy=False,
    )
