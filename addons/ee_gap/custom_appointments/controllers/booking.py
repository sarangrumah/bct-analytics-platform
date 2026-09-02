# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from odoo import http
from odoo.http import request


class BookingPortal(http.Controller):
    @http.route("/book/<string:slug>", type="http", auth="public", website=True)
    def show_type(self, slug):
        atype = (
            request.env["appointment.type"]
            .sudo()
            .search(
                [("slug", "=", slug), ("active", "=", True)],
                limit=1,
            )
        )
        if not atype:
            return request.not_found()
        # Build a 7-day slot grid using the first available resource
        resource = atype.resource_ids.filtered("active")[:1]
        slots = self._build_slots(atype, resource) if resource else []
        return request.render(
            "custom_appointments.booking_page",
            {"atype": atype, "resource": resource, "slots": slots},
        )

    @http.route("/book/<string:slug>/submit", type="http", auth="public", website=True, methods=["POST"], csrf=True)
    def submit(self, slug, **post):
        atype = (
            request.env["appointment.type"]
            .sudo()
            .search(
                [("slug", "=", slug), ("active", "=", True)],
                limit=1,
            )
        )
        if not atype:
            return request.not_found()
        start = datetime.fromisoformat(post["start_dt"])
        end = start + timedelta(minutes=atype.duration_minutes)
        resource_id = int(post["resource_id"])
        booking = (
            request.env["appointment.booking"]
            .sudo()
            .create(
                {
                    "type_id": atype.id,
                    "resource_id": resource_id,
                    "customer_name": post["customer_name"],
                    "customer_email": post["customer_email"],
                    "customer_phone": post.get("customer_phone"),
                    "start_dt": start,
                    "end_dt": end,
                    "notes": post.get("notes", ""),
                }
            )
        )
        return request.render(
            "custom_appointments.booking_confirm",
            {"booking": booking, "atype": atype},
        )

    def _build_slots(self, atype, resource):
        """Generate the next ~5 day × hourly slot grid (simplified)."""
        if not resource:
            return []
        out = []
        now = datetime.utcnow()
        cutoff = now + timedelta(hours=atype.advance_notice_hours)
        for day in range(1, min(atype.max_days_ahead + 1, 6)):
            base = (now + timedelta(days=day)).replace(hour=0, minute=0, second=0, microsecond=0)
            iso_dow = base.isoweekday()
            if str(iso_dow) not in resource.working_days.split(","):
                continue
            start_h = int(resource.working_hours_start)
            end_h = int(resource.working_hours_end)
            for h in range(start_h, end_h):
                slot_dt = base.replace(hour=h)
                if slot_dt < cutoff:
                    continue
                out.append(slot_dt.isoformat())
        return out
