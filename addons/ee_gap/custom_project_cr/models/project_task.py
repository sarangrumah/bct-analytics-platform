# -*- coding: utf-8 -*-
"""A task's parent may be a project or a change request -- never nothing."""

from odoo import api, fields, models


class ProjectTask(models.Model):
    _inherit = "project.task"

    change_request_id = fields.Many2one(
        "custom.change.request",
        string="Change Request",
        index=True,
        ondelete="set null",
        help="Set when this task exists because a brand asked for a change.",
    )
    custom_cr_code = fields.Char(
        related="change_request_id.code",
        store=True,
        readonly=True,
        string="CR Number",
    )

    @api.onchange("change_request_id")
    def _onchange_change_request(self):
        """Inherit the brand and the project from the request."""
        for task in self:
            if task.change_request_id:
                task.custom_vertical_id = task.change_request_id.vertical_id
                task.custom_source = "cr"
                if task.change_request_id.project_id and not task.project_id:
                    task.project_id = task.change_request_id.project_id
