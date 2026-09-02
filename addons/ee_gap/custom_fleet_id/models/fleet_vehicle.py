# -*- coding: utf-8 -*-
import json
import logging
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


# Indonesia plate format: 1-2 area letters, space, 1-4 digits, space, 1-3 letters.
# E.g. "B 1234 ABC", "AB 12 X", "DK 9999 ZZZ"
ID_PLATE_REGEX = re.compile(r"^[A-Z]{1,2}\s\d{1,4}\s[A-Z]{1,3}$")


class FleetVehicle(models.Model):
    _inherit = ["fleet.vehicle"]

    # ---------- STNK (Surat Tanda Nomor Kendaraan) ----------
    x_stnk_number = fields.Char(string="STNK Number")
    x_stnk_expiry_date = fields.Date(string="STNK Expiry", tracking=True)
    x_stnk_alert_days_before = fields.Integer(
        string="STNK Alert (days before)",
        default=30,
        help="Days before STNK expiry to mark as 'expiring'.",
    )
    x_stnk_status = fields.Selection(
        [
            ("valid", "Valid"),
            ("expiring", "Expiring"),
            ("expired", "Expired"),
        ],
        string="STNK Status",
        compute="_compute_x_stnk_status",
        store=True,
    )

    # ---------- KIR (Kartu Uji Berkala) ----------
    x_kir_number = fields.Char(string="KIR Number", help="Kartu Uji Berkala")
    x_kir_expiry_date = fields.Date(string="KIR Expiry", tracking=True)
    x_kir_alert_days_before = fields.Integer(
        string="KIR Alert (days before)",
        default=30,
        help="Days before KIR expiry to mark as 'expiring'.",
    )
    x_kir_status = fields.Selection(
        [
            ("valid", "Valid"),
            ("expiring", "Expiring"),
            ("expired", "Expired"),
            ("na", "N/A"),
        ],
        string="KIR Status",
        compute="_compute_x_kir_status",
        store=True,
        default="na",
    )

    # ---------- BBM (fuel) ----------
    x_bbm_type = fields.Selection(
        [
            ("pertalite", "Pertalite"),
            ("pertamax", "Pertamax"),
            ("pertamax_turbo", "Pertamax Turbo"),
            ("dex", "Dex"),
            ("dexlite", "Dexlite"),
            ("solar", "Solar"),
            ("listrik", "Listrik (EV)"),
        ],
        string="BBM Type",
        default="pertalite",
    )

    # ---------- Driver assignment ----------
    x_driver_partner_id = fields.Many2one(
        "res.partner",
        string="Assigned Driver",
        tracking=True,
    )

    # ---------- BBM logs & Driver assignment history ----------
    x_bbm_log_ids = fields.One2many(
        "custom.fleet.bbm.log",
        "vehicle_id",
        string="BBM Logs",
    )
    x_bbm_log_count = fields.Integer(
        string="BBM Logs",
        compute="_compute_x_bbm_log_count",
    )
    x_driver_assignment_ids = fields.One2many(
        "custom.fleet.driver.assignment",
        "vehicle_id",
        string="Driver Assignment History",
    )
    x_driver_assignment_count = fields.Integer(
        string="Driver Assignments",
        compute="_compute_x_driver_assignment_count",
    )

    # ---------- Service due tracking ----------
    x_current_odometer = fields.Float(
        string="Current Odometer (km)",
        help="Latest odometer reading in kilometers.",
    )
    x_next_service_km = fields.Integer(
        string="Next Service (km)",
        help="Odometer threshold at which the next service is due.",
    )
    x_next_service_date = fields.Date(
        string="Next Service Date",
    )
    x_service_due = fields.Boolean(
        string="Service Due",
        compute="_compute_x_service_due",
        store=True,
        help="True when current odometer >= next service km or today >= next service date.",
    )

    # ---------- Computes ----------

    @api.depends("x_stnk_expiry_date", "x_stnk_alert_days_before")
    def _compute_x_stnk_status(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if not rec.x_stnk_expiry_date:
                rec.x_stnk_status = False
                continue
            alert_days = rec.x_stnk_alert_days_before or 0
            delta = (rec.x_stnk_expiry_date - today).days
            if delta < 0:
                rec.x_stnk_status = "expired"
            elif delta <= alert_days:
                rec.x_stnk_status = "expiring"
            else:
                rec.x_stnk_status = "valid"

    @api.depends("x_kir_expiry_date", "x_kir_alert_days_before")
    def _compute_x_kir_status(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if not rec.x_kir_expiry_date:
                rec.x_kir_status = "na"
                continue
            alert_days = rec.x_kir_alert_days_before or 0
            delta = (rec.x_kir_expiry_date - today).days
            if delta < 0:
                rec.x_kir_status = "expired"
            elif delta <= alert_days:
                rec.x_kir_status = "expiring"
            else:
                rec.x_kir_status = "valid"

    @api.depends("x_bbm_log_ids")
    def _compute_x_bbm_log_count(self):
        for rec in self:
            rec.x_bbm_log_count = len(rec.x_bbm_log_ids)

    @api.depends("x_driver_assignment_ids")
    def _compute_x_driver_assignment_count(self):
        for rec in self:
            rec.x_driver_assignment_count = len(rec.x_driver_assignment_ids)

    @api.depends("x_current_odometer", "x_next_service_km", "x_next_service_date")
    def _compute_x_service_due(self):
        today = fields.Date.context_today(self)
        for rec in self:
            due = False
            if rec.x_next_service_km and rec.x_current_odometer:
                if rec.x_current_odometer >= rec.x_next_service_km:
                    due = True
            if not due and rec.x_next_service_date:
                if rec.x_next_service_date <= today:
                    due = True
            rec.x_service_due = due

    # ---------- Indonesia plate format constraint (warning, non-blocking) ----------

    @api.constrains("license_plate")
    def _check_id_plate_format(self):
        """Warn (chatter) when license_plate doesn't match Indonesia format.

        Per spec: warning only, do not block. We post a note to the chatter
        instead of raising. We still raise a UserError ONLY if explicitly
        requested via context flag, to keep the helper unit-testable.
        """
        for rec in self:
            plate = (rec.license_plate or "").strip().upper()
            if not plate:
                continue
            if ID_PLATE_REGEX.match(plate):
                continue
            msg = _("License plate '%(p)s' does not match Indonesia format (e.g. 'B 1234 ABC'). Please verify.") % {
                "p": rec.license_plate
            }
            if self.env.context.get("custom_fleet_id_strict_plate"):
                raise UserError(msg)
            try:
                rec.message_post(body=msg, subtype_xmlid="mail.mt_note")
            except Exception:  # pragma: no cover
                _logger.warning("plate format warning post failed for %s", rec.id)

    # ---------- Actions ----------

    def action_open_bbm_logs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("BBM Logs"),
            "res_model": "custom.fleet.bbm.log",
            "view_mode": "list,form",
            "domain": [("vehicle_id", "=", self.id)],
            "context": {"default_vehicle_id": self.id},
        }

    def action_open_driver_assignments(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Driver Assignments"),
            "res_model": "custom.fleet.driver.assignment",
            "view_mode": "list,form",
            "domain": [("vehicle_id", "=", self.id)],
            "context": {"default_vehicle_id": self.id},
        }

    def action_add_bbm_log(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Add BBM Log"),
            "res_model": "custom.fleet.bbm.log",
            "view_mode": "form",
            "target": "new",
            "context": {"default_vehicle_id": self.id},
        }

    # ---------- PDP audit + driver assignment history on driver change ----------

    def write(self, vals):
        old_drivers = {}
        if "x_driver_partner_id" in vals:
            old_drivers = {r.id: r.x_driver_partner_id.id for r in self}
        res = super().write(vals)
        if "x_driver_partner_id" in vals:
            new_id = vals.get("x_driver_partner_id")
            for rec in self:
                old_id = old_drivers.get(rec.id)
                if old_id != new_id:
                    rec._pdp_audit_driver_change(old_id, new_id)
                    rec._sync_driver_assignment_history(old_id, new_id)
        return res

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # Seed driver assignment history on initial driver
        for rec in records:
            if rec.x_driver_partner_id:
                rec._sync_driver_assignment_history(False, rec.x_driver_partner_id.id)
        return records

    def _sync_driver_assignment_history(self, old_id, new_id):
        """End any active assignment, then create a new one for the new driver."""
        Assignment = self.env["custom.fleet.driver.assignment"].sudo()
        today = fields.Date.context_today(self)
        active = Assignment.search(
            [
                ("vehicle_id", "=", self.id),
                ("status", "=", "active"),
            ]
        )
        for a in active:
            a.write(
                {
                    "end_date": today,
                    "status": "transferred" if new_id else "ended",
                }
            )
        if new_id:
            Assignment.create(
                {
                    "vehicle_id": self.id,
                    "partner_id": new_id,
                    "start_date": today,
                    "status": "active",
                }
            )

    def _pdp_audit_driver_change(self, old_id, new_id):
        try:
            user = self.env.user
            payload = {
                "old_driver_id": old_id,
                "new_driver_id": new_id,
                "vehicle": self.display_name,
            }
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
            _logger.warning("fleet driver audit log failed: %s", e)

    # ---------- Maintenance integration helpers ----------

    def _maintenance_available(self):
        """Return True if the standard `maintenance` module is installed."""
        return bool(
            self.env["ir.module.module"]
            .sudo()
            .search_count([("name", "=", "maintenance"), ("state", "=", "installed")])
        )

    def _create_stnk_kir_maintenance_request(self, reason_lines):
        """Create a maintenance.request for STNK/KIR renewal.

        Idempotent per vehicle/day: if a request with the same title already
        exists and is not done, do not duplicate.
        """
        if not self._maintenance_available():
            return False
        Request = self.env["maintenance.request"].sudo()
        title = _("STNK/KIR Renewal Needed: %s") % (self.license_plate or self.display_name or self.id)
        existing = Request.search(
            [
                ("name", "=", title),
                ("stage_id.done", "=", False),
            ],
            limit=1,
        )
        if existing:
            return existing
        vals = {
            "name": title,
            "description": "<br/>".join(reason_lines),
        }
        # Some Odoo versions/configs require maintenance_type
        if "maintenance_type" in Request._fields:
            vals["maintenance_type"] = "preventive"
        try:
            return Request.create(vals)
        except Exception as e:  # pragma: no cover
            _logger.warning("maintenance request create failed for vehicle %s: %s", self.id, e)
            return False

    # ---------- Cron ----------

    @api.model
    def cron_check_expiry(self):
        """Post reminders to vehicles whose STNK or KIR is expiring or expired.

        If the maintenance module is installed, also auto-create a
        maintenance.request when STNK or KIR expiry falls within 30 days.
        """
        today = fields.Date.context_today(self)
        threshold_30 = today + timedelta(days=30)
        vehicles = self.search(
            [
                "|",
                ("x_stnk_status", "in", ("expiring", "expired")),
                ("x_kir_status", "in", ("expiring", "expired")),
            ]
        )
        maintenance_on = vehicles and vehicles[0]._maintenance_available()
        for rec in vehicles:
            lines = []
            if rec.x_stnk_status in ("expiring", "expired"):
                lines.append(
                    _("STNK %(num)s is %(st)s (expiry %(d)s)")
                    % {
                        "num": rec.x_stnk_number or "-",
                        "st": rec.x_stnk_status,
                        "d": rec.x_stnk_expiry_date or "-",
                    }
                )
            if rec.x_kir_status in ("expiring", "expired"):
                lines.append(
                    _("KIR %(num)s is %(st)s (expiry %(d)s)")
                    % {
                        "num": rec.x_kir_number or "-",
                        "st": rec.x_kir_status,
                        "d": rec.x_kir_expiry_date or "-",
                    }
                )
            if lines:
                rec.message_post(
                    body="<b>%s</b><br/>%s"
                    % (
                        _("Fleet Expiry Reminder"),
                        "<br/>".join(lines),
                    ),
                    subtype_xmlid="mail.mt_note",
                )
            # Auto-create maintenance request if within 30 days
            if maintenance_on:
                trigger_lines = []
                if rec.x_stnk_expiry_date and rec.x_stnk_expiry_date <= threshold_30:
                    trigger_lines.append(_("STNK expires on %s") % rec.x_stnk_expiry_date)
                if rec.x_kir_expiry_date and rec.x_kir_expiry_date <= threshold_30:
                    trigger_lines.append(_("KIR expires on %s") % rec.x_kir_expiry_date)
                if trigger_lines:
                    rec._create_stnk_kir_maintenance_request(trigger_lines)
        return True

    @api.model
    def cron_check_service_due(self):
        """Flag vehicles whose next service is approaching and post a note."""
        today = fields.Date.context_today(self)
        soon = today + timedelta(days=14)
        # Vehicles with a date within 14 days, OR odometer reaching threshold soon
        vehicles = self.search(
            [
                "|",
                "&",
                ("x_next_service_date", "!=", False),
                ("x_next_service_date", "<=", soon),
                ("x_service_due", "=", True),
            ]
        )
        for rec in vehicles:
            parts = []
            if rec.x_next_service_date:
                parts.append(_("next service date: %s") % rec.x_next_service_date)
            if rec.x_next_service_km:
                parts.append(
                    _("next service km: %(t)s (current %(c)s)")
                    % {
                        "t": rec.x_next_service_km,
                        "c": int(rec.x_current_odometer or 0),
                    }
                )
            body = "<b>%s</b><br/>%s" % (
                _("Service Due Reminder"),
                "; ".join(parts) or _("Service is due."),
            )
            rec.message_post(body=body, subtype_xmlid="mail.mt_note")
        return True
