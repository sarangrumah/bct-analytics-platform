# -*- coding: utf-8 -*-
"""Extensions to `repair.order` for internal asset maintenance: equipment
link + maintenance.request bridge, SLA, MRP, cost analysis, quality check,
and rework flow."""

from __future__ import annotations

import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class RepairOrder(models.Model):
    _inherit = "repair.order"

    # ---------- Internal asset link ----------
    x_equipment_id = fields.Many2one(
        "maintenance.equipment",
        string="Asset / Equipment",
        tracking=True,
        index="btree_not_null",
        help="Internal asset being repaired. Bridges this repair to the maintenance module.",
    )
    x_maintenance_request_id = fields.Many2one(
        "maintenance.request",
        string="Maintenance Request",
        readonly=True,
        copy=False,
        help="Corrective maintenance request auto-created for the linked asset when this repair is confirmed.",
    )

    # ---------- Internal requester ----------
    x_requesting_user_id = fields.Many2one(
        "res.users",
        string="Requested By",
        default=lambda self: self.env.user,
        tracking=True,
    )
    x_requesting_team_id = fields.Many2one(
        "maintenance.team",
        string="Requesting Team",
    )

    # ---------- SLA fields ----------
    x_promised_completion_date = fields.Date(
        string="Promised Completion Date",
        tracking=True,
    )
    x_actual_completion_date = fields.Datetime(
        string="Actual Completion Date",
        readonly=True,
    )
    x_sla_status = fields.Selection(
        [
            ("on_track", "On Track"),
            ("at_risk", "At Risk"),
            ("breached", "Breached"),
            ("done", "Done"),
        ],
        string="SLA Status",
        compute="_compute_sla_status",
        store=True,
    )

    # ---------- Fault description (internal) ----------
    x_id_complaint = fields.Text(
        string="Fault Description (Internal)",
        help="Internal description of the fault / reason for the repair.",
    )

    # ---------- Cost analysis ----------
    x_labor_hours = fields.Float(string="Labor Hours", default=0.0)
    x_labor_rate = fields.Float(
        string="Labor Rate (per hour)",
        default=lambda self: self._default_labor_rate(),
    )
    x_material_cost = fields.Float(
        string="Material Cost",
        compute="_compute_total_repair_cost",
        store=True,
    )
    x_labor_cost = fields.Float(
        string="Labor Cost",
        compute="_compute_total_repair_cost",
        store=True,
    )
    x_total_repair_cost = fields.Float(
        string="Total Repair Cost",
        compute="_compute_total_repair_cost",
        store=True,
    )

    # ---------- Rework (re-opened) ----------
    x_returned = fields.Boolean(
        string="Re-opened / Rework",
        default=False,
        tracking=True,
        readonly=True,
        copy=False,
    )
    x_return_date = fields.Datetime(
        string="Rework Date",
        readonly=True,
        copy=False,
    )
    x_return_reason = fields.Text(string="Rework Reason")

    # ---------- MRP link ----------
    x_mrp_production_id = fields.Many2one(
        "mrp.production",
        string="Work Order (Manufacturing)",
        readonly=True,
        copy=False,
    )

    # ---------- Quality check link ----------
    x_quality_check_ids = fields.One2many(
        "quality.check",
        compute="_compute_quality_check_ids",
        string="Quality Checks",
    )
    x_quality_check_count = fields.Integer(
        compute="_compute_quality_check_ids",
    )

    # ====================================================================
    # Defaults
    # ====================================================================

    @api.model
    def _default_labor_rate(self):
        ICP = self.env["ir.config_parameter"].sudo()
        try:
            return float(ICP.get_param("custom_repairs.labor_rate", "100000"))
        except (TypeError, ValueError):
            return 100000.0

    # ====================================================================
    # SLA compute
    # ====================================================================

    @api.depends("x_promised_completion_date", "state", "x_actual_completion_date")
    def _compute_sla_status(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.state == "done" or rec.x_actual_completion_date:
                rec.x_sla_status = "done"
                continue
            if not rec.x_promised_completion_date:
                rec.x_sla_status = "on_track"
                continue
            promised = rec.x_promised_completion_date
            if today > promised:
                rec.x_sla_status = "breached"
            elif (promised - today) <= timedelta(days=1):
                rec.x_sla_status = "at_risk"
            else:
                rec.x_sla_status = "on_track"

    # ====================================================================
    # Cost compute
    # ====================================================================

    def _material_cost_field_candidates(self):
        """Field names on repair material lines that may hold unit/total cost.

        Different Odoo versions / installs expose different field names
        (price_unit, price_subtotal, x_material_cost, etc.). We try each
        gracefully.
        """
        return ("price_subtotal", "price_total", "price_unit")

    def _material_line_records(self):
        """Return material-line records for the repair.

        Odoo 19 uses `move_ids` (stock.move) on repair.order. Older variants
        used `operations` / `parts_lines`. We probe what exists.
        """
        self.ensure_one()
        for candidate in ("move_ids", "operations", "parts_lines"):
            if candidate in self._fields:
                return self[candidate]
        return self.env["stock.move"].browse()

    @api.depends("x_labor_hours", "x_labor_rate")
    def _compute_total_repair_cost(self):
        for rec in self:
            material = 0.0
            try:
                lines = rec._material_line_records()
                for line in lines:
                    qty = getattr(line, "product_uom_qty", 0.0) or getattr(line, "quantity", 0.0) or 0.0
                    unit_cost = 0.0
                    product = getattr(line, "product_id", False)
                    if product and getattr(product, "standard_price", None) is not None:
                        unit_cost = product.standard_price or 0.0
                    if not unit_cost:
                        for fname in rec._material_cost_field_candidates():
                            if fname in line._fields:
                                val = line[fname] or 0.0
                                if val:
                                    unit_cost = val if fname == "price_unit" else 0.0
                                    if fname != "price_unit":
                                        # field already represents subtotal
                                        material += val
                                        unit_cost = 0.0
                                    break
                    if unit_cost:
                        material += unit_cost * qty
            except Exception as exc:  # pragma: no cover (defensive)
                _logger.debug("repair cost material compute fallback: %s", exc)
                material = 0.0
            labor = (rec.x_labor_hours or 0.0) * (rec.x_labor_rate or 0.0)
            rec.x_material_cost = material
            rec.x_labor_cost = labor
            rec.x_total_repair_cost = material + labor

    # ====================================================================
    # Quality check link
    # ====================================================================

    def _compute_quality_check_ids(self):
        has_quality = "quality.check" in self.env
        for rec in self:
            if not has_quality:
                rec.x_quality_check_ids = False
                rec.x_quality_check_count = 0
                continue
            QC = self.env["quality.check"].sudo()
            checks = (
                QC.search(
                    [
                        ("name", "like", rec.name or rec.display_name or ""),
                    ]
                )
                if (rec.name or rec.display_name)
                else QC.browse()
            )
            rec.x_quality_check_ids = checks
            rec.x_quality_check_count = len(checks)

    # ====================================================================
    # Write override: capture actual completion + trigger quality check
    # ====================================================================

    def write(self, vals):
        new_state = vals.get("state")
        if new_state == "done":
            vals.setdefault("x_actual_completion_date", fields.Datetime.now())
        res = super().write(vals)
        if new_state == "done":
            for rec in self:
                rec._maybe_launch_quality_check()
        if new_state == "confirmed":
            for rec in self:
                rec._maybe_create_mrp_workorder()
                rec._maybe_create_maintenance_request()
        return res

    # ====================================================================
    # MRP auto-create work-order
    # ====================================================================

    def _has_material_requirements(self):
        self.ensure_one()
        lines = self._material_line_records()
        for line in lines:
            qty = getattr(line, "product_uom_qty", 0.0) or getattr(line, "quantity", 0.0) or 0.0
            if qty and qty > 0:
                return True
        return False

    def _maybe_create_mrp_workorder(self):
        """Create a stub mrp.production when spare parts are required.

        Best-effort: silently skip when `mrp` is not installed or required
        fields cannot be resolved.
        """
        self.ensure_one()
        if self.x_mrp_production_id:
            return
        if "mrp.production" not in self.env:
            return
        if not self._has_material_requirements():
            return
        MrpProduction = self.env["mrp.production"].sudo()
        product = self.product_id
        if not product:
            return
        try:
            production = MrpProduction.create(
                {
                    "product_id": product.id,
                    "product_qty": 1.0,
                    "product_uom_id": product.uom_id.id if product.uom_id else False,
                    "origin": self.name or self.display_name or "",
                }
            )
        except Exception as exc:  # pragma: no cover (defensive)
            _logger.info("custom_repairs: mrp.production stub create skipped (%s)", exc)
            return
        self.x_mrp_production_id = production.id
        _logger.info(
            "custom_repairs: created mrp.production %s for repair %s",
            production.id,
            self.display_name,
        )

    # ====================================================================
    # Maintenance request bridge
    # ====================================================================

    def _maybe_create_maintenance_request(self):
        """Open a corrective maintenance.request on the linked asset.

        Best-effort and idempotent: silently skips when no equipment is
        linked, when `maintenance` is not installed, or when the request
        can't be created (e.g. no default team resolvable).
        """
        self.ensure_one()
        if self.x_maintenance_request_id:
            return
        if "maintenance.request" not in self.env or not self.x_equipment_id:
            return
        Request = self.env["maintenance.request"].sudo()
        owner = self.x_requesting_user_id or self.user_id or self.env.user
        try:
            request = Request.create(
                {
                    "name": _("Repair %s") % (self.name or self.display_name or ""),
                    "equipment_id": self.x_equipment_id.id,
                    "maintenance_type": "corrective",
                    "request_date": fields.Date.context_today(self),
                    "description": self.x_id_complaint or "",
                    "owner_user_id": owner.id,
                    "company_id": self.company_id.id,
                }
            )
        except Exception as exc:  # pragma: no cover (defensive)
            _logger.info("custom_repairs: maintenance.request create skipped (%s)", exc)
            return
        self.x_maintenance_request_id = request.id
        _logger.info(
            "custom_repairs: created maintenance.request %s for repair %s",
            request.id,
            self.display_name,
        )

    def action_view_maintenance_request(self):
        """Open the linked maintenance request."""
        self.ensure_one()
        if not self.x_maintenance_request_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": "maintenance.request",
            "res_id": self.x_maintenance_request_id.id,
            "view_mode": "form",
            "target": "current",
        }

    # ====================================================================
    # Quality check on completion
    # ====================================================================

    def _maybe_launch_quality_check(self):
        """Conditionally create a quality.check on done if module present."""
        self.ensure_one()
        if "quality.check" not in self.env or "quality.point" not in self.env:
            return False
        QPoint = self.env["quality.point"].sudo()
        QCheck = self.env["quality.check"].sudo()
        domain = []
        if self.product_id:
            domain = [("product_id", "=", self.product_id.id)]
        point = QPoint.search(domain, limit=1) if domain else QPoint.search([], limit=1)
        if not point:
            return False
        try:
            check = QCheck.create(
                {
                    "point_id": point.id,
                    "note": _("Auto-launched for repair %s") % (self.display_name or ""),
                }
            )
        except Exception as exc:  # pragma: no cover (defensive)
            _logger.info("custom_repairs: quality.check create skipped (%s)", exc)
            return False
        _logger.info(
            "custom_repairs: launched quality.check %s for repair %s",
            check.id,
            self.display_name,
        )
        return check

    # ====================================================================
    # Rework flow
    # ====================================================================

    def action_set_rework(self):
        """Mark the repair as re-opened for rework."""
        for rec in self:
            rec.write(
                {
                    "x_returned": True,
                    "x_return_date": fields.Datetime.now(),
                }
            )
            rec.message_post(
                body=_("Repair re-opened for rework."),
            )
        return True
