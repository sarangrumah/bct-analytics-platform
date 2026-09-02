# -*- coding: utf-8 -*-
import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


IOT_OPS = [
    ("gt", "Greater than"),
    ("lt", "Less than"),
    ("eq", "Equal to"),
]

PREDICTED_VIA = [
    ("mtbf", "MTBF history"),
    ("iot", "IoT thresholds"),
    ("manual", "Manual"),
]


class MaintenanceEquipment(models.Model):
    _name = "maintenance.equipment"
    _inherit = ["maintenance.equipment", "mail.thread"]

    x_iot_threshold_metric = fields.Char(
        string="IoT Threshold Metric",
        help="Metric name from custom_iot_bridge (e.g. 'temperature_c').",
        tracking=True,
    )
    x_iot_threshold_value = fields.Float(
        string="IoT Threshold Value",
        tracking=True,
    )
    x_iot_threshold_op = fields.Selection(
        IOT_OPS,
        string="IoT Threshold Operator",
        default="gt",
        tracking=True,
    )
    x_auto_request_on_breach = fields.Boolean(
        string="Auto Create Request on Breach",
        default=False,
        tracking=True,
    )
    x_last_iot_breach = fields.Datetime(
        string="Last IoT Breach",
        readonly=True,
    )

    # ---------- MTBF / MTTR ----------

    x_total_failures = fields.Integer(
        string="Total Failures",
        compute="_compute_failure_stats",
        store=True,
        help="Total number of corrective (done) maintenance requests.",
    )
    x_last_failure_at = fields.Datetime(
        string="Last Failure At",
        compute="_compute_failure_stats",
        store=True,
    )
    x_mtbf_hours = fields.Float(
        string="MTBF (hours)",
        compute="_compute_failure_stats",
        store=True,
        help="Mean Time Between Failures: operating window divided by number of failures.",
    )
    x_mttr_hours = fields.Float(
        string="MTTR (hours)",
        compute="_compute_failure_stats",
        store=True,
        help="Mean Time To Repair: average hours between request creation and close.",
    )

    # ---------- Predictive maintenance ----------

    x_predicted_via = fields.Selection(
        PREDICTED_VIA,
        string="Predicted Via",
        compute="_compute_predicted_next_maintenance",
        store=True,
    )
    x_predicted_next_maintenance = fields.Date(
        string="Predicted Next Maintenance",
        compute="_compute_predicted_next_maintenance",
        store=True,
        help="Computed from MTBF (mtbf_hours / 8h per day) added to last maintenance.",
    )

    @api.depends(
        "maintenance_ids",
        "maintenance_ids.stage_id.done",
        "maintenance_ids.maintenance_type",
        "maintenance_ids.close_date",
        "maintenance_ids.request_date",
        "effective_date",
    )
    def _compute_failure_stats(self):
        Request = self.env["maintenance.request"]
        for eq in self:
            done_corrective = Request.search(
                [
                    ("equipment_id", "=", eq.id),
                    ("maintenance_type", "=", "corrective"),
                    ("stage_id.done", "=", True),
                ],
                order="close_date asc",
            )
            count = len(done_corrective)
            eq.x_total_failures = count

            last_failure = False
            mttr_total_hours = 0.0
            mttr_n = 0
            for r in done_corrective:
                close_dt = r.close_date or r.request_date
                if close_dt and (not last_failure or close_dt > last_failure):
                    last_failure = close_dt
                # MTTR per request
                if r.request_date and r.close_date:
                    delta = (r.close_date - r.request_date).total_seconds() / 3600.0
                    if delta > 0:
                        mttr_total_hours += delta
                        mttr_n += 1
            eq.x_last_failure_at = last_failure or False
            eq.x_mttr_hours = (mttr_total_hours / mttr_n) if mttr_n else 0.0

            # MTBF: operating window (effective_date or first failure -> last failure) / failures
            if count > 0 and last_failure:
                start_dt = False
                if eq.effective_date:
                    start_dt = fields.Datetime.to_datetime(eq.effective_date)
                if not start_dt:
                    first = done_corrective[0]
                    start_dt = first.request_date or first.close_date
                if start_dt:
                    window_hours = (last_failure - start_dt).total_seconds() / 3600.0
                    if window_hours > 0:
                        eq.x_mtbf_hours = window_hours / count
                    else:
                        eq.x_mtbf_hours = 0.0
                else:
                    eq.x_mtbf_hours = 0.0
            else:
                eq.x_mtbf_hours = 0.0

    @api.depends("x_mtbf_hours", "x_last_iot_breach", "x_iot_threshold_metric", "effective_date", "x_last_failure_at")
    def _compute_predicted_next_maintenance(self):
        for eq in self:
            via = "manual"
            predicted = False
            base_date = eq.x_last_failure_at and fields.Date.to_date(eq.x_last_failure_at)
            if not base_date and eq.effective_date:
                base_date = fields.Date.to_date(eq.effective_date)
            if eq.x_mtbf_hours and eq.x_mtbf_hours > 0 and base_date:
                days = int(eq.x_mtbf_hours / 8.0)
                if days > 0:
                    predicted = base_date + timedelta(days=days)
                    via = "mtbf"
            if not predicted and eq.x_iot_threshold_metric:
                via = "iot"
            eq.x_predicted_next_maintenance = predicted
            eq.x_predicted_via = via

    def action_schedule_predicted_maintenance(self):
        """Create a draft preventive maintenance.request based on predicted date."""
        Request = self.env["maintenance.request"]
        created = self.env["maintenance.request"]
        for eq in self:
            target = eq.x_predicted_next_maintenance or fields.Date.context_today(self)
            req = Request.create(
                {
                    "name": _("Predicted Maintenance: %s") % eq.display_name,
                    "equipment_id": eq.id,
                    "maintenance_type": "preventive",
                    "schedule_date": fields.Datetime.to_datetime(target),
                    "description": _(
                        "Auto-scheduled from MTBF prediction.\n"
                        "MTBF (hours): %(mtbf).2f\n"
                        "Predicted via: %(via)s\n"
                        "Predicted date: %(date)s"
                    )
                    % {
                        "mtbf": eq.x_mtbf_hours or 0.0,
                        "via": eq.x_predicted_via or "manual",
                        "date": target,
                    },
                    "maintenance_team_id": eq.maintenance_team_id.id or False,
                }
            )
            created |= req
        if len(created) == 1:
            return {
                "type": "ir.actions.act_window",
                "res_model": "maintenance.request",
                "res_id": created.id,
                "view_mode": "form",
                "target": "current",
            }
        return {
            "type": "ir.actions.act_window",
            "res_model": "maintenance.request",
            "domain": [("id", "in", created.ids)],
            "view_mode": "list,form",
        }

    # ---------- PDP audit on owner / responsible user change ----------

    def write(self, vals):
        tracked = ("owner_user_id", "employee_id", "department_id")
        old = {}
        if any(k in vals for k in tracked):
            old = {r.id: {k: (r[k].id if hasattr(r[k], "id") else r[k]) for k in tracked} for r in self}
        res = super().write(vals)
        if old:
            for rec in self:
                changes = {}
                for k in tracked:
                    new_val = rec[k].id if hasattr(rec[k], "id") else rec[k]
                    if old[rec.id][k] != new_val:
                        changes[k] = {"old": old[rec.id][k], "new": new_val}
                if changes:
                    rec._pdp_audit_owner_change(changes)
        return res

    def _pdp_audit_owner_change(self, changes):
        try:
            user = self.env.user
            payload = {"changes": changes, "ref": self.name}
            self.env.cr.execute(
                """
                INSERT INTO pdp.audit_log (
                    actor_user_id, actor_login, tenant_db,
                    model_name, res_id, action,
                    field_changes, classification
                ) VALUES (%s, %s, %s, %s, %s, 'write', %s::jsonb, 'internal')
                """,
                (
                    user.id if user else None,
                    user.login if user else None,
                    self.env.cr.dbname,
                    self._name,
                    self.id,
                    json.dumps(payload),
                ),
            )
        except Exception as e:  # pragma: no cover
            _logger.warning("maintenance equipment audit log failed: %s", e)

    # ---------- IoT breach scanning cron ----------

    @api.model
    def cron_check_iot_breaches(self):
        """Scan equipment with IoT thresholds; create requests on breach."""
        equipments = self.search(
            [
                ("x_iot_threshold_metric", "!=", False),
                ("x_iot_threshold_op", "!=", False),
            ]
        )
        if not equipments:
            _logger.info("custom_maintenance: no equipment with IoT thresholds.")
            return

        Reading = self.env.get("iot.reading")
        if Reading is None:
            _logger.info("custom_maintenance: iot.reading model not found; skipping breach scan.")
            return

        Request = self.env["maintenance.request"]
        for eq in equipments:
            metric = eq.x_iot_threshold_metric
            op = eq.x_iot_threshold_op or "gt"
            threshold = eq.x_iot_threshold_value or 0.0

            domain = [("metric", "=", metric)]
            if eq.x_last_iot_breach:
                domain.append(("recorded_at", ">", eq.x_last_iot_breach))
            latest = Reading.sudo().search(domain, order="recorded_at desc", limit=1)
            if not latest:
                continue

            breached = False
            if op == "gt" and latest.value > threshold:
                breached = True
            elif op == "lt" and latest.value < threshold:
                breached = True
            elif op == "eq" and latest.value == threshold:
                breached = True

            if not breached:
                continue

            eq.sudo().write({"x_last_iot_breach": latest.recorded_at})

            if not eq.x_auto_request_on_breach:
                _logger.info(
                    "custom_maintenance: breach on equipment %s (%s %s %s, value=%s) but auto-request disabled.",
                    eq.display_name,
                    metric,
                    op,
                    threshold,
                    latest.value,
                )
                continue

            Request.sudo().create(
                {
                    "name": _("IoT breach: %s (%s)") % (eq.display_name, metric),
                    "equipment_id": eq.id,
                    "maintenance_type": "corrective",
                    "priority": "2",
                    "description": _(
                        "Auto-created from IoT reading.\n"
                        "Metric: %(metric)s\nOperator: %(op)s\n"
                        "Threshold: %(thr)s\nObserved value: %(val)s\n"
                        "Recorded at: %(ts)s"
                    )
                    % {
                        "metric": metric,
                        "op": op,
                        "thr": threshold,
                        "val": latest.value,
                        "ts": latest.recorded_at,
                    },
                    "x_iot_triggered": True,
                    "x_iot_metric_value": latest.value,
                }
            )
            _logger.info(
                "custom_maintenance: created request for equipment %s (value=%s)",
                eq.display_name,
                latest.value,
            )
