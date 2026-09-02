# -*- coding: utf-8 -*-
"""Link rental.asset to maintenance.equipment + default quality point.

Exposes failure-history aggregates (computed by custom_maintenance) on
the rental.asset record so rental managers can see drone condition at
a glance without leaving the asset form.
"""

from __future__ import annotations

from odoo import api, fields, models


class RentalAsset(models.Model):
    _inherit = "rental.asset"

    equipment_id = fields.Many2one(
        "maintenance.equipment",
        string="Maintenance Equipment",
        help="Linked maintenance equipment record. Failure history "
        "(MTBF, MTTR, total_failures) is shown via related fields.",
    )
    default_quality_point_id = fields.Many2one(
        "quality.point",
        string="Default Quality Point",
        help="Template used when auto-spawning quality.check on rental return.",
    )

    # Read-only mirrors of maintenance metrics for in-rental visibility
    failure_count = fields.Integer(
        related="equipment_id.x_total_failures",
        string="Total Failures",
        store=False,
        readonly=True,
    )
    last_failure_at = fields.Datetime(
        related="equipment_id.x_last_failure_at",
        string="Last Failure",
        store=False,
        readonly=True,
    )
    mtbf_hours = fields.Float(
        related="equipment_id.x_mtbf_hours",
        string="MTBF (h)",
        store=False,
        readonly=True,
    )

    # Condition grade — quick at-a-glance flag, distinct from formal quality.check
    condition_grade = fields.Selection(
        [
            ("new", "100% — Brand New"),
            ("good", "80-99% — Good"),
            ("fair", "50-79% — Fair (minor history)"),
            ("poor", "<50% — Poor (significant history)"),
        ],
        default="new",
        string="Condition Grade",
        tracking=True,
        help="Coarse grade. Refresh via the 'Recompute Grade' button which "
        "derives the value from total_failures and last quality.check result.",
    )

    def action_recompute_condition_grade(self):
        """Heuristic grade: failures-driven downgrade."""
        Check = self.env["quality.check"].sudo()
        for rec in self:
            failures = rec.failure_count or 0
            last_check = Check.search(
                [
                    ("product_id", "=", rec.product_id.id),
                    ("state", "in", ("pass", "fail")),
                ],
                order="performed_at desc",
                limit=1,
            )
            if last_check.state == "fail":
                rec.condition_grade = "poor"
            elif failures >= 5:
                rec.condition_grade = "poor"
            elif failures >= 2:
                rec.condition_grade = "fair"
            elif failures >= 1:
                rec.condition_grade = "good"
            else:
                rec.condition_grade = "new"

    @api.model
    def _ensure_equipment_for_asset(self, asset):
        """Create a maintenance.equipment for the asset if not linked yet."""
        if asset.equipment_id:
            return asset.equipment_id
        Equipment = self.env["maintenance.equipment"].sudo()
        eq = Equipment.create(
            {
                "name": asset.name,
                "serial_no": asset.serial_number or "",
                "company_id": asset.company_id.id,
            }
        )
        asset.sudo().equipment_id = eq.id
        return eq

    def action_create_equipment(self):
        for rec in self:
            self._ensure_equipment_for_asset(rec)
        return True
