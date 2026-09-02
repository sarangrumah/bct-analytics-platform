# -*- coding: utf-8 -*-
import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class CustomTimesheetWeeklySummary(models.Model):
    _name = "custom.timesheet.weekly.summary"
    _description = "Custom Timesheet Weekly AI Summary"
    _inherit = ["mail.thread"]
    _order = "week_start desc, project_id asc"

    name = fields.Char(
        string="Reference",
        compute="_compute_name",
        store=True,
    )
    project_id = fields.Many2one(
        "project.project",
        string="Project",
        required=True,
        ondelete="cascade",
    )
    week_start = fields.Date(
        string="Week Start",
        required=True,
        help="Monday of the ISO week summarized by this record.",
        tracking=True,
    )
    week_end = fields.Date(
        string="Week End",
        compute="_compute_week_end",
        store=True,
    )
    total_hours = fields.Float(string="Total Hours", compute="_compute_aggregates", store=True)
    billable_hours = fields.Float(string="Billable Hours", compute="_compute_aggregates", store=True)
    overtime_hours = fields.Float(string="Overtime Hours", compute="_compute_aggregates", store=True)
    line_count = fields.Integer(string="Lines Count", compute="_compute_aggregates", store=True)
    summary_html = fields.Html(string="AI Summary", sanitize=True, tracking=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("summarized", "Summarized"),
        ],
        default="draft",
        tracking=True,
    )
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)

    _uniq_project_week = models.Constraint(
        "unique(project_id, week_start, company_id)",
        "A weekly summary already exists for this project and week.",
    )

    @api.depends("project_id", "week_start")
    def _compute_name(self):
        for rec in self:
            if rec.project_id and rec.week_start:
                rec.name = "%s - W/C %s" % (
                    rec.project_id.name,
                    fields.Date.to_string(rec.week_start),
                )
            else:
                rec.name = _("New Weekly Summary")

    @api.depends("week_start")
    def _compute_week_end(self):
        for rec in self:
            rec.week_end = (rec.week_start + timedelta(days=6)) if rec.week_start else False

    @api.depends("project_id", "week_start", "week_end")
    def _compute_aggregates(self):
        AAL = self.env["account.analytic.line"].sudo()
        for rec in self:
            if not (rec.project_id and rec.week_start and rec.week_end):
                rec.total_hours = 0.0
                rec.billable_hours = 0.0
                rec.overtime_hours = 0.0
                rec.line_count = 0
                continue
            lines = AAL.search(
                [
                    ("project_id", "=", rec.project_id.id),
                    ("date", ">=", rec.week_start),
                    ("date", "<=", rec.week_end),
                ]
            )
            rec.line_count = len(lines)
            rec.total_hours = sum(lines.mapped("unit_amount"))
            rec.billable_hours = sum(lines.filtered(lambda l: l.x_billable).mapped("unit_amount"))
            rec.overtime_hours = sum(lines.mapped("x_overtime_hours"))

    # ------------------------------------------------------------------
    # AI summarization
    # ------------------------------------------------------------------

    def _collect_payload(self):
        self.ensure_one()
        AAL = self.env["account.analytic.line"].sudo()
        lines = AAL.search(
            [
                ("project_id", "=", self.project_id.id),
                ("date", ">=", self.week_start),
                ("date", "<=", self.week_end),
            ]
        )
        return {
            "project": self.project_id.name or "",
            "week_start": fields.Date.to_string(self.week_start),
            "week_end": fields.Date.to_string(self.week_end),
            "totals": {
                "lines": len(lines),
                "hours": self.total_hours,
                "billable_hours": self.billable_hours,
                "overtime_hours": self.overtime_hours,
            },
            "entries": [line._custom_ai_payload() for line in lines[:200]],
        }

    def action_ai_summarize(self):
        self.ensure_one()
        try:
            result = self.env["custom.ai"]._recommend(
                model="custom.timesheet.weekly.summary",
                res_id=self.id,
                payload=self._collect_payload(),
            )
        except Exception as e:
            _logger.error("AI summarize failed: %s", e)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("AI Unavailable"),
                    "message": str(e),
                    "type": "warning",
                },
            }
        text = result.get("summary") or result.get("response") or result.get("text") or json.dumps(result)[:2000]
        # Minimal HTML wrap.
        self.summary_html = "<div class='o_ai_summary'>%s</div>" % text
        self.state = "summarized"
        self.message_post(body=_("<b>AI Weekly Summary</b><br/>%s") % text)
        return True

    # Convenience: build/refresh weekly summaries for a project.
    @api.model
    def _build_for_project_week(self, project_id, week_start):
        rec = self.search(
            [("project_id", "=", project_id), ("week_start", "=", week_start)],
            limit=1,
        )
        if not rec:
            rec = self.create({"project_id": project_id, "week_start": week_start})
        else:
            rec._compute_aggregates()
        return rec
