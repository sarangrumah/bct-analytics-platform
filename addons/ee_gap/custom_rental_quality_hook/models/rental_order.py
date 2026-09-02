# -*- coding: utf-8 -*-
"""Auto-spawn quality.check on rental return + quick maintenance.request shortcut."""

from __future__ import annotations

from odoo import _, fields, models
from odoo.exceptions import UserError


class RentalOrder(models.Model):
    _inherit = "rental.order"

    quality_check_id = fields.Many2one(
        "quality.check",
        string="Return Quality Check",
        copy=False,
        readonly=True,
    )
    maintenance_request_id = fields.Many2one(
        "maintenance.request",
        string="Maintenance Request",
        copy=False,
        readonly=True,
    )

    def _spawn_return_quality_check(self):
        """Create a quality.check from asset's default_quality_point_id.

        Skips silently if no point template is configured.
        """
        self.ensure_one()
        point = self.asset_id.default_quality_point_id
        if not point or self.quality_check_id:
            return self.env["quality.check"]
        check = (
            self.env["quality.check"]
            .sudo()
            .create(
                {
                    "point_id": point.id,
                    "user_id": self.env.user.id,
                    "company_id": self.company_id.id,
                    "note": _("Auto-spawned on return of rental %s") % self.name,
                }
            )
        )
        # Seed inspection lines from the point's default test template
        if point.default_test_id:
            try:
                check.action_apply_test_template(point.default_test_id.id)
            except Exception:
                pass
        self.quality_check_id = check.id
        self.message_post(
            body=_("Quality check %s spawned for return inspection.") % check.name,
        )
        return check

    def action_return(self):
        res = super().action_return()
        for rec in self:
            if rec.state == "returned":
                rec._spawn_return_quality_check()
        return res

    def action_open_quality_check(self):
        self.ensure_one()
        if not self.quality_check_id:
            raise UserError(_("No quality check linked. Configure asset.default_quality_point_id first."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "quality.check",
            "res_id": self.quality_check_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_create_maintenance_request(self):
        """Open or create a corrective maintenance.request linked to the asset's equipment."""
        self.ensure_one()
        if self.maintenance_request_id:
            return {
                "type": "ir.actions.act_window",
                "res_model": "maintenance.request",
                "res_id": self.maintenance_request_id.id,
                "view_mode": "form",
                "target": "current",
            }
        equipment = self.asset_id.equipment_id or self.env["rental.asset"]._ensure_equipment_for_asset(self.asset_id)
        req = (
            self.env["maintenance.request"]
            .sudo()
            .create(
                {
                    "name": _("Damage reported on rental %s") % self.name,
                    "equipment_id": equipment.id,
                    "maintenance_type": "corrective",
                    "description": _(
                        "Reported during rental return.\nRental: %(rental)s\nCustomer: %(cust)s\nReturn date: %(date)s"
                    )
                    % {
                        "rental": self.name,
                        "cust": self.partner_id.name,
                        "date": self.return_dt_actual or "TBD",
                    },
                }
            )
        )
        self.maintenance_request_id = req.id
        self.asset_id.sudo().write({"state": "maintenance"})
        self.message_post(body=_("Maintenance request %s created.") % req.id)
        return {
            "type": "ir.actions.act_window",
            "res_model": "maintenance.request",
            "res_id": req.id,
            "view_mode": "form",
            "target": "current",
        }
