# -*- coding: utf-8 -*-
"""Public kiosk portal for PIN-based attendance toggling.

Route: /custom_attendance/kiosk

Notes:
- ``auth='public'`` so a tablet on the lobby can render the page without a
  logged-in Odoo session.
- Real authentication is the 4-digit PIN attached to ``hr.employee``.
- Optional ``lat`` / ``lng`` POST params are persisted on the attendance.
- A session id (cookie ``custom_attendance_kiosk``) is recorded on each
  record for traceability.
"""

import math
import secrets

from odoo import http
from odoo.http import request


class AttendanceKioskController(http.Controller):
    _COOKIE_NAME = "custom_attendance_kiosk"

    # ------------------------------------------------------------------
    # GET: render kiosk form
    # ------------------------------------------------------------------
    @http.route(
        "/custom_attendance/kiosk",
        type="http",
        auth="public",
        website=False,
        csrf=False,
        methods=["GET"],
    )
    def kiosk_page(self, **kw):
        response = request.render(
            "custom_attendance.kiosk_page",
            {"message": kw.get("message", ""), "status": kw.get("status", "")},
        )
        if not request.httprequest.cookies.get(self._COOKIE_NAME):
            response.set_cookie(
                self._COOKIE_NAME,
                secrets.token_urlsafe(16),
                max_age=60 * 60 * 24 * 30,
                httponly=True,
                samesite="Lax",
            )
        return response

    # ------------------------------------------------------------------
    # POST: validate PIN + toggle attendance
    # ------------------------------------------------------------------
    @http.route(
        "/custom_attendance/kiosk/submit",
        type="http",
        auth="public",
        website=False,
        csrf=False,
        methods=["POST"],
    )
    def kiosk_submit(self, pin=None, lat=None, lng=None, **kw):
        pin = (pin or "").strip()
        session_id = request.httprequest.cookies.get(self._COOKIE_NAME) or ""

        if not pin or not pin.isdigit() or len(pin) < 4:
            return request.redirect("/custom_attendance/kiosk?status=error&message=Invalid+PIN")

        Attendance = request.env["hr.attendance"].sudo()
        employee = Attendance._kiosk_resolve_employee_by_pin(pin)
        if not employee:
            return request.redirect("/custom_attendance/kiosk?status=error&message=PIN+not+recognised")

        try:
            # nosemgrep: python.flask.security.injection.nan-injection.nan-injection - non-finite (nan/inf) rejected by isfinite guard below
            lat_f = float(lat) if lat else None
            # nosemgrep: python.flask.security.injection.nan-injection.nan-injection - non-finite (nan/inf) rejected by isfinite guard below
            lng_f = float(lng) if lng else None
            if (lat_f is not None and not math.isfinite(lat_f)) or (lng_f is not None and not math.isfinite(lng_f)):
                lat_f = lng_f = None
        except (TypeError, ValueError):
            lat_f = lng_f = None

        try:
            _rec, action = Attendance._kiosk_toggle(
                employee=employee,
                lat=lat_f,
                lng=lng_f,
                session_id=session_id,
            )
        except Exception as exc:
            return request.redirect(
                "/custom_attendance/kiosk?status=error&message=%s" % (str(exc).replace(" ", "+"))[:160]
            )

        verb = "Checked+in" if action == "check_in" else "Checked+out"
        return request.redirect(
            "/custom_attendance/kiosk?status=ok&message=%s+%s" % (verb, (employee.name or "").replace(" ", "+"))
        )
