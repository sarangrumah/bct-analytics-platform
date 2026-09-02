# -*- coding: utf-8 -*-
import logging
import math
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class HrAttendance(models.Model):
    _name = "hr.attendance"
    _inherit = ["hr.attendance", "mail.thread", "mail.activity.mixin"]

    # ------------------------------------------------------------------
    # Geofence (existing)
    # ------------------------------------------------------------------
    x_check_in_lat = fields.Float(string="Check-In Latitude", digits=(10, 7))
    x_check_in_lng = fields.Float(string="Check-In Longitude", digits=(10, 7))
    x_check_out_lat = fields.Float(string="Check-Out Latitude", digits=(10, 7))
    x_check_out_lng = fields.Float(string="Check-Out Longitude", digits=(10, 7))
    x_geofence_id = fields.Many2one(
        "attendance.geofence",
        string="Geofence",
    )
    x_geofence_validated = fields.Boolean(
        string="Geofence Validated",
        compute="_compute_geofence_validated",
        store=True,
    )
    x_overtime_hours = fields.Float(
        string="Overtime Hours",
        compute="_compute_overtime_hours",
        store=True,
    )

    # ------------------------------------------------------------------
    # Kiosk
    # ------------------------------------------------------------------
    x_kiosk_session = fields.Char(
        string="Kiosk Session",
        help="Opaque session identifier for kiosk-originated check-ins.",
    )

    # ------------------------------------------------------------------
    # Approval workflow
    # ------------------------------------------------------------------
    x_approval_required = fields.Boolean(
        string="Approval Required",
        compute="_compute_approval_required",
        store=True,
        tracking=True,
    )
    x_approval_state = fields.Selection(
        [
            ("draft", "Draft"),
            ("pending", "Pending"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
        ],
        string="Approval State",
        default="draft",
        tracking=True,
        copy=False,
    )
    x_approval_by = fields.Many2one(
        "res.users",
        string="Approved/Rejected By",
        readonly=True,
        copy=False,
        tracking=True,
    )
    x_approval_note = fields.Text(string="Approval Note", tracking=True)

    # ------------------------------------------------------------------
    # Face recognition
    # ------------------------------------------------------------------
    x_face_recognition_data = fields.Binary(
        string="Face Recognition Snapshot",
        attachment=True,
    )
    x_face_recognition_confidence = fields.Float(
        string="Face Recognition Confidence",
        readonly=True,
        copy=False,
    )

    # ------------------------------------------------------------------
    # Payroll integration
    # ------------------------------------------------------------------
    x_payroll_work_entry_id = fields.Many2one(
        "hr.work.entry",
        string="Payroll Work Entry",
        readonly=True,
        copy=False,
    )
    x_payroll_synced = fields.Boolean(
        string="Synced to Payroll",
        readonly=True,
        copy=False,
        tracking=True,
    )

    # ==================================================================
    # Geofence
    # ==================================================================
    @staticmethod
    def _haversine_meters(lat1, lon1, lat2, lon2):
        """Great-circle distance between two points in meters."""
        earth_radius_m = 6371000.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)
        a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
        c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
        return earth_radius_m * c

    @api.depends(
        "x_geofence_id",
        "x_geofence_id.latitude",
        "x_geofence_id.longitude",
        "x_geofence_id.radius_meters",
        "x_check_in_lat",
        "x_check_in_lng",
    )
    def _compute_geofence_validated(self):
        for rec in self:
            geofence = rec.x_geofence_id
            if not geofence:
                rec.x_geofence_validated = False
                continue
            if not rec.x_check_in_lat and not rec.x_check_in_lng:
                rec.x_geofence_validated = False
                continue
            distance = self._haversine_meters(
                geofence.latitude,
                geofence.longitude,
                rec.x_check_in_lat,
                rec.x_check_in_lng,
            )
            rec.x_geofence_validated = distance <= (geofence.radius_meters or 0)

    # ==================================================================
    # Overtime hours from configured rule
    # ==================================================================
    @api.model
    def _get_active_overtime_rule(self, check_in=None):
        """Return the best-matching active overtime rule for the period."""
        Rule = self.env["custom.attendance.overtime.rule"].sudo()
        differential = "weekday"
        if check_in:
            if check_in.weekday() >= 5:
                differential = "weekend"
        domain = [
            ("is_active", "=", True),
            ("differential", "=", differential),
            ("company_id", "in", [False, self.env.company.id]),
        ]
        rule = Rule.search(domain, order="sequence, id", limit=1)
        if not rule:
            rule = Rule.search(
                [("is_active", "=", True), ("company_id", "in", [False, self.env.company.id])],
                order="sequence, id",
                limit=1,
            )
        return rule

    @api.depends("worked_hours", "check_in")
    def _compute_overtime_hours(self):
        for rec in self:
            rule = rec._get_active_overtime_rule(rec.check_in)
            threshold = rule.threshold_hours if rule else 8.0
            rec.x_overtime_hours = max(0.0, (rec.worked_hours or 0.0) - threshold)

    # ==================================================================
    # Approval workflow
    # ==================================================================
    @api.depends("worked_hours", "check_in")
    def _compute_approval_required(self):
        for rec in self:
            required = False
            if (rec.worked_hours or 0.0) > 12.0:
                required = True
            elif rec.check_in:
                hour = rec.check_in.hour
                if hour >= 22 or hour < 5:
                    required = True
            rec.x_approval_required = required

    def action_request_approval(self):
        for rec in self:
            if rec.x_approval_state not in ("draft", "rejected"):
                raise UserError(_("Approval can only be requested from draft or rejected state."))
            rec.x_approval_state = "pending"
            # Notify manager via activity
            manager_user = False
            if rec.employee_id and rec.employee_id.parent_id:
                manager_user = rec.employee_id.parent_id.user_id
            if not manager_user:
                manager_user = self.env.user
            try:
                rec.activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=_("Attendance approval requested"),
                    note=_("Anomalous attendance for %(emp)s (worked %(h).2f h) requires your review.")
                    % {
                        "emp": rec.employee_id.name or "",
                        "h": rec.worked_hours or 0.0,
                    },
                    user_id=manager_user.id,
                )
            except Exception as exc:  # pragma: no cover - mail not critical
                _logger.warning("Could not schedule approval activity: %s", exc)
        return True

    def action_approve(self):
        for rec in self:
            if rec.x_approval_state != "pending":
                raise UserError(_("Only pending approvals can be approved."))
            rec.write(
                {
                    "x_approval_state": "approved",
                    "x_approval_by": self.env.user.id,
                }
            )
        return True

    def action_reject(self):
        for rec in self:
            if rec.x_approval_state != "pending":
                raise UserError(_("Only pending approvals can be rejected."))
            rec.write(
                {
                    "x_approval_state": "rejected",
                    "x_approval_by": self.env.user.id,
                }
            )
        return True

    # ==================================================================
    # Overtime -> hr.work.entry
    # ==================================================================
    def _ensure_overtime_work_entry_type(self):
        WET = self.env["hr.work.entry.type"].sudo()
        wet = WET.search([("code", "=", "OT")], limit=1)
        if not wet:
            vals = {"name": "Overtime", "code": "OT"}
            if "display_code" in WET._fields:
                vals["display_code"] = "OT"
            wet = WET.create(vals)
        return wet

    def action_create_overtime_work_entry(self):
        """Create / refresh an hr.work.entry for overtime hours."""
        self.ensure_one()
        if (self.x_overtime_hours or 0.0) <= 0.0:
            if hasattr(self, "message_post"):
                self.message_post(body=_("No overtime hours to push to payroll."))
            return False
        if not self.employee_id:
            raise UserError(_("Cannot create a work entry without an employee linked."))
        if not self.check_in:
            raise UserError(_("Attendance has no check-in datetime."))

        WorkEntry = self.env["hr.work.entry"].sudo()
        wet = self._ensure_overtime_work_entry_type()

        # Idempotency: cancel previously linked work entry.
        if self.x_payroll_work_entry_id:
            try:
                self.x_payroll_work_entry_id.sudo().state = "cancelled"
            except Exception as exc:  # pragma: no cover
                _logger.warning("Could not cancel previous work entry: %s", exc)

        date_start = self.check_in
        date_stop = date_start + timedelta(hours=self.x_overtime_hours)
        vals = {
            "name": _("Overtime %s") % (self.employee_id.name or ""),
            "employee_id": self.employee_id.id,
            "date_start": date_start,
            "date_stop": date_stop,
            "duration": self.x_overtime_hours,
            "work_entry_type_id": wet.id,
            "state": "draft",
        }
        if "date" in WorkEntry._fields:
            vals["date"] = fields.Date.to_date(date_start)
        if "x_source_attendance_id" in WorkEntry._fields:
            vals["x_source_attendance_id"] = self.id
        work_entry = WorkEntry.create(vals)
        self.write(
            {
                "x_payroll_work_entry_id": work_entry.id,
                "x_payroll_synced": True,
            }
        )
        if hasattr(self, "message_post"):
            self.message_post(
                body=_("Created overtime work entry <b>%(name)s</b> (%(hours).2f h).")
                % {
                    "name": work_entry.display_name or work_entry.name,
                    "hours": self.x_overtime_hours,
                }
            )
        return work_entry

    def unlink(self):
        # Cancel linked payroll work entries before destroying source.
        for rec in self:
            if rec.x_payroll_work_entry_id:
                try:
                    rec.x_payroll_work_entry_id.sudo().state = "cancelled"
                except Exception as exc:  # pragma: no cover
                    _logger.warning("Could not cancel work entry on unlink: %s", exc)
        return super().unlink()

    # ==================================================================
    # Face recognition stub
    # ==================================================================
    def action_verify_face(self):
        """Bridge to custom.ai gateway for face verification."""
        self.ensure_one()
        if not self.x_face_recognition_data:
            raise UserError(_("No face snapshot stored on this attendance."))
        try:
            result = self.env["custom.ai"]._recommend(
                model="hr.attendance",
                res_id=self.id,
                payload={
                    "face_image": "<<binary>>",
                    "expected_employee_id": self.employee_id.id or False,
                },
            )
        except Exception as exc:
            _logger.warning("custom.ai face verify failed: %s", exc)
            return False
        confidence = 0.0
        if isinstance(result, dict):
            confidence = float(
                result.get("confidence") or result.get("score") or (result.get("data") or {}).get("confidence") or 0.0
            )
        self.x_face_recognition_confidence = confidence
        if confidence < 0.6:
            self.x_approval_required = True
            if hasattr(self, "message_post"):
                self.message_post(body=_("Face confidence %.2f below threshold 0.60; approval required.") % confidence)
        return confidence

    # ==================================================================
    # Kiosk helper API
    # ==================================================================
    @api.model
    def _kiosk_resolve_employee_by_pin(self, pin):
        if not pin:
            return self.env["hr.employee"]
        Employee = self.env["hr.employee"].sudo()
        return Employee.search([("pin", "=", str(pin).strip())], limit=1)

    @api.model
    def _kiosk_toggle(self, employee, lat=None, lng=None, session_id=None):
        """Toggle check-in/out for an employee from the kiosk."""
        if not employee:
            raise UserError(_("Unknown employee."))
        AttendanceSudo = self.sudo()
        open_att = AttendanceSudo.search(
            [("employee_id", "=", employee.id), ("check_out", "=", False)],
            limit=1,
        )
        now = fields.Datetime.now()
        if open_att:
            vals = {"check_out": now}
            if lat is not None:
                vals["x_check_out_lat"] = float(lat)
            if lng is not None:
                vals["x_check_out_lng"] = float(lng)
            open_att.write(vals)
            return open_att, "check_out"
        vals = {
            "employee_id": employee.id,
            "check_in": now,
        }
        if lat is not None:
            vals["x_check_in_lat"] = float(lat)
        if lng is not None:
            vals["x_check_in_lng"] = float(lng)
        if session_id:
            vals["x_kiosk_session"] = session_id
        rec = AttendanceSudo.create(vals)
        return rec, "check_in"
