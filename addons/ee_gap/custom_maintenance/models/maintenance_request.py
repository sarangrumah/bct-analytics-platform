# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class MaintenanceRequest(models.Model):
    _name = "maintenance.request"
    _inherit = ["maintenance.request", "mail.thread"]

    # ---------- IoT fields (existing) ----------

    x_iot_triggered = fields.Boolean(
        string="IoT Triggered",
        default=False,
        readonly=True,
        tracking=True,
    )
    x_iot_metric_value = fields.Float(
        string="IoT Metric Value",
        readonly=True,
    )
    x_priority_score = fields.Integer(
        string="Priority Score",
        compute="_compute_priority_score",
        store=True,
        help="Composite ranking score derived from priority and stage.",
    )

    # ---------- Spare parts ----------

    x_spare_part_ids = fields.Many2many(
        "product.product",
        "custom_maintenance_request_spare_part_rel",
        "request_id",
        "product_id",
        string="Spare Parts",
        domain="[('type', '=', 'consu')]",
        help="Consumable parts used to fulfil this maintenance.",
    )

    # ---------- SLA deadlines ----------

    x_sla_id = fields.Many2one(
        "custom.maintenance.team.sla",
        string="SLA Policy",
        compute="_compute_sla",
        store=True,
    )
    x_sla_response_deadline = fields.Datetime(
        string="SLA Response Deadline",
        compute="_compute_sla_deadlines",
        store=True,
    )
    x_sla_resolve_deadline = fields.Datetime(
        string="SLA Resolve Deadline",
        compute="_compute_sla_deadlines",
        store=True,
    )
    x_sla_status = fields.Selection(
        [
            ("ok", "On Track"),
            ("warn", "Approaching"),
            ("breach", "Breached"),
            ("done", "Done"),
        ],
        compute="_compute_sla_status",
        store=True,
    )
    x_sla_breach_notified = fields.Boolean(
        string="SLA Breach Notified",
        default=False,
        copy=False,
    )

    # ---------- Cost tracking ----------

    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )
    x_labor_cost = fields.Monetary(
        string="Labor Cost",
        currency_field="currency_id",
    )
    x_parts_cost = fields.Monetary(
        string="Parts Cost",
        currency_field="currency_id",
        compute="_compute_parts_cost",
        store=True,
    )
    x_total_cost = fields.Monetary(
        string="Total Cost",
        currency_field="currency_id",
        compute="_compute_total_cost",
        store=True,
    )

    # ---------- Compute: priority score ----------

    @api.depends("priority", "stage_id", "stage_id.done", "x_iot_triggered")
    def _compute_priority_score(self):
        for rec in self:
            try:
                base = int(rec.priority or "0")
            except (TypeError, ValueError):
                base = 0
            score = base * 10
            if rec.stage_id and rec.stage_id.done:
                score -= 50
            if rec.x_iot_triggered:
                score += 5
            rec.x_priority_score = score

    # ---------- Compute: SLA ----------

    @api.depends("priority", "maintenance_team_id")
    def _compute_sla(self):
        Sla = self.env["custom.maintenance.team.sla"]
        for rec in self:
            rec.x_sla_id = Sla._find_for(rec.maintenance_team_id.id, rec.priority or "2")

    @api.depends("x_sla_id", "x_sla_id.response_hours", "x_sla_id.resolve_hours", "create_date", "request_date")
    def _compute_sla_deadlines(self):
        for rec in self:
            base = rec.create_date or rec.request_date
            if rec.x_sla_id and base:
                base_dt = fields.Datetime.to_datetime(base)
                rec.x_sla_response_deadline = base_dt + timedelta(hours=rec.x_sla_id.response_hours)
                rec.x_sla_resolve_deadline = base_dt + timedelta(hours=rec.x_sla_id.resolve_hours)
            else:
                rec.x_sla_response_deadline = False
                rec.x_sla_resolve_deadline = False

    @api.depends("x_sla_resolve_deadline", "stage_id", "stage_id.done", "close_date")
    def _compute_sla_status(self):
        now = fields.Datetime.now()
        for rec in self:
            if rec.stage_id and rec.stage_id.done:
                rec.x_sla_status = "done"
                continue
            if not rec.x_sla_resolve_deadline:
                rec.x_sla_status = "ok"
                continue
            delta = (rec.x_sla_resolve_deadline - now).total_seconds() / 3600.0
            if delta <= 0:
                rec.x_sla_status = "breach"
            elif delta <= 1.0:
                rec.x_sla_status = "warn"
            else:
                rec.x_sla_status = "ok"

    # ---------- Compute: cost ----------

    @api.depends("x_spare_part_ids", "x_spare_part_ids.list_price")
    def _compute_parts_cost(self):
        for rec in self:
            rec.x_parts_cost = sum(rec.x_spare_part_ids.mapped("list_price") or [0.0])

    @api.depends("x_labor_cost", "x_parts_cost")
    def _compute_total_cost(self):
        for rec in self:
            rec.x_total_cost = (rec.x_labor_cost or 0.0) + (rec.x_parts_cost or 0.0)

    # ---------- On done: stock moves for spare parts ----------

    def write(self, vals):
        prev_done = {r.id: bool(r.stage_id and r.stage_id.done) for r in self}
        res = super().write(vals)
        if "stage_id" in vals:
            for rec in self:
                now_done = bool(rec.stage_id and rec.stage_id.done)
                if now_done and not prev_done.get(rec.id) and rec.x_spare_part_ids:
                    rec._create_spare_part_stock_moves()
        return res

    def _create_spare_part_stock_moves(self):
        """Create stock.move records for the used spare parts (if stock installed)."""
        self.ensure_one()
        Move = self.env.get("stock.move")
        Location = self.env.get("stock.location")
        if Move is None or Location is None:
            _logger.info("custom_maintenance: stock module not installed; skipping moves.")
            return
        src = Location.sudo().search([("usage", "=", "internal")], limit=1)
        dst = Location.sudo().search([("usage", "=", "production")], limit=1) or Location.sudo().search(
            [("usage", "=", "inventory")], limit=1
        )
        if not src or not dst:
            _logger.info("custom_maintenance: missing source/dest stock locations; skipping.")
            return
        for product in self.x_spare_part_ids:
            Move.sudo().create(
                {
                    "name": _("Maintenance: %s") % self.name,
                    "product_id": product.id,
                    "product_uom_qty": 1.0,
                    "product_uom": product.uom_id.id,
                    "location_id": src.id,
                    "location_dest_id": dst.id,
                    "origin": self.name,
                }
            )

    # ---------- Cron: SLA breach check ----------

    @api.model
    def cron_check_sla_breach(self):
        """Recompute SLA status and notify managers on newly-breached requests."""
        open_reqs = self.search([("stage_id.done", "=", False), ("x_sla_resolve_deadline", "!=", False)])
        if not open_reqs:
            return True
        open_reqs._compute_sla_status()
        breached = open_reqs.filtered(lambda r: r.x_sla_status == "breach" and not r.x_sla_breach_notified)
        for rec in breached:
            try:
                rec.message_post(
                    body=_(
                        "<b>SLA Breach</b><br/>This maintenance request has passed its SLA resolution deadline (%s)."
                    )
                    % rec.x_sla_resolve_deadline,
                    subtype_xmlid="mail.mt_comment",
                )
                # Notify team manager via mail if available
                manager = rec.maintenance_team_id and rec.maintenance_team_id.member_ids[:1]
                if manager and manager.work_email:
                    self.env["mail.mail"].sudo().create(
                        {
                            "subject": _("[Maintenance SLA Breach] %s") % rec.name,
                            "body_html": _(
                                "<p>Request <b>%(name)s</b> for equipment "
                                "<b>%(eq)s</b> has breached its SLA "
                                "resolution deadline (%(dl)s).</p>"
                            )
                            % {
                                "name": rec.name,
                                "eq": rec.equipment_id.display_name or "",
                                "dl": rec.x_sla_resolve_deadline,
                            },
                            "email_to": manager.work_email,
                        }
                    ).send()
            except Exception as e:  # pragma: no cover
                _logger.warning("SLA breach notification failed: %s", e)
            rec.x_sla_breach_notified = True
        return True
