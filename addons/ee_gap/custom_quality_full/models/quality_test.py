# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class CustomQualityTest(models.Model):
    """Reusable inspection-test template (a set of questions). The lines can
    be applied to a quality.point to seed the default inspection lines, or to
    a live quality.check."""

    _name = "custom.quality.test"
    _description = "Quality Test Template"
    _order = "sequence, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    code = fields.Char(index=True)
    test_type = fields.Selection(
        [
            ("visual", "Visual"),
            ("dimensional", "Dimensional"),
            ("functional", "Functional"),
        ],
        default="visual",
        required=True,
    )
    description = fields.Text()
    company_id = fields.Many2one(
        "res.company",
        default=lambda s: s.env.company,
    )
    active = fields.Boolean(default=True)
    line_ids = fields.One2many(
        "custom.quality.test.line",
        "test_id",
        string="Questions",
        copy=True,
    )

    def apply_to_check(self, check):
        """Copy the test lines onto a quality.check as inspection lines."""
        self.ensure_one()
        if not check:
            raise UserError(_("No quality.check provided."))
        Line = self.env["custom.quality.inspection.line"]
        for tline in self.line_ids:
            Line.create(
                {
                    "check_id": check.id,
                    "sequence": tline.sequence,
                    "question": tline.name,
                    "response_type": tline.response_type,
                    "is_required": tline.is_required,
                    "expected_min": tline.expected_min,
                    "expected_max": tline.expected_max,
                    "expected_set": tline.expected_set,
                }
            )
        return True


class CustomQualityTestLine(models.Model):
    _name = "custom.quality.test.line"
    _description = "Quality Test Template Line"
    _order = "sequence, id"

    test_id = fields.Many2one(
        "custom.quality.test",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Question", required=True)
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
    expected_set = fields.Text()
    note = fields.Text()
