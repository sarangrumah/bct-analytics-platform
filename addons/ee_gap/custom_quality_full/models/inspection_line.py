# -*- coding: utf-8 -*-
from odoo import api, fields, models


class CustomQualityInspectionLine(models.Model):
    """A single question / measurement attached to a quality.check, allowing
    multi-line inspection checklists with min/max ranges or accepted-value
    sets."""

    _name = "custom.quality.inspection.line"
    _description = "Quality Inspection Line"
    _order = "check_id, sequence, id"

    check_id = fields.Many2one(
        "quality.check",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    question = fields.Char(required=True)
    response_type = fields.Selection(
        [
            ("text", "Free Text"),
            ("number", "Numeric"),
            ("boolean", "Yes / No"),
            ("photo", "Photo"),
            ("select", "Selection"),
        ],
        default="boolean",
        required=True,
    )
    is_required = fields.Boolean(default=True)
    expected_min = fields.Float()
    expected_max = fields.Float()
    expected_set = fields.Text(
        string="Expected Values",
        help="Comma-separated list of accepted values (response_type=select).",
    )
    actual_value = fields.Char()
    actual_photo = fields.Binary(attachment=True)
    actual_photo_filename = fields.Char()
    pass_fail = fields.Selection(
        [
            ("pass", "Pass"),
            ("fail", "Fail"),
            ("na", "N/A"),
        ],
        compute="_compute_pass_fail",
        store=True,
    )
    note = fields.Char()

    @api.depends(
        "response_type", "actual_value", "actual_photo", "expected_min", "expected_max", "expected_set", "is_required"
    )
    def _compute_pass_fail(self):
        for line in self:
            if not line.is_required and not line.actual_value and not line.actual_photo:
                line.pass_fail = "na"
                continue
            rt = line.response_type
            val = (line.actual_value or "").strip()
            if rt == "boolean":
                line.pass_fail = "pass" if val.lower() in ("1", "true", "yes", "ok", "pass") else "fail"
            elif rt == "number":
                try:
                    n = float(val)
                except (TypeError, ValueError):
                    line.pass_fail = "fail" if line.is_required else "na"
                    continue
                ok = True
                if line.expected_min and n < line.expected_min:
                    ok = False
                if line.expected_max and n > line.expected_max:
                    ok = False
                line.pass_fail = "pass" if ok else "fail"
            elif rt == "text":
                line.pass_fail = "pass" if val else ("fail" if line.is_required else "na")
            elif rt == "photo":
                line.pass_fail = "pass" if line.actual_photo else ("fail" if line.is_required else "na")
            elif rt == "select":
                allowed = [v.strip() for v in (line.expected_set or "").split(",") if v.strip()]
                line.pass_fail = "pass" if val in allowed else "fail"
            else:
                line.pass_fail = "na"
