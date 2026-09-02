# -*- coding: utf-8 -*-
"""#18 Maintenance report (weekly/monthly) — drone & battery health.

One row per equipment (drone / battery), aggregating the maintenance requests in
the report period and reading the reliability metrics already computed on
``maintenance.equipment`` (MTBF / MTTR / failures — read, not recomputed here).

SLA status values on ``maintenance.request`` are ok / warn / breach / done.
"""

from odoo import models


class CustomReportMaintenanceHealth(models.AbstractModel):
    _name = "custom.report.maintenance.health"
    _inherit = "custom.report.engine"
    _description = "Maintenance Health Report"

    _report_code = "maintenance_health"
    _report_title = "Maintenance Report"

    def _xlsx_columns(self):
        return [
            {"header": "Equipment", "field": "equipment", "kind": "text", "width": 30},
            {"header": "Category", "field": "category", "kind": "text", "width": 18},
            {"header": "Requests", "field": "requests", "kind": "number", "width": 10},
            {"header": "MTBF (h)", "field": "mtbf", "kind": "number", "width": 12},
            {"header": "MTTR (h)", "field": "mttr", "kind": "number", "width": 12},
            {"header": "Failures", "field": "failures", "kind": "number", "width": 10},
            {"header": "Last Failure", "field": "last_failure", "kind": "text", "width": 16},
            {"header": "SLA Breach", "field": "sla_breach", "kind": "number", "width": 12},
            {"header": "Labor", "field": "labor", "kind": "number", "width": 14},
            {"header": "Parts", "field": "parts", "kind": "number", "width": 14},
            {"header": "Total Cost", "field": "total", "kind": "number", "width": 16},
        ]

    def _build_lines(self, filters):
        domain = [
            ("request_date", ">=", filters["date_from"]),
            ("request_date", "<=", filters["date_to"]),
        ]
        if filters.get("company_ids"):
            domain.append(("company_id", "in", filters["company_ids"] + [False]))

        requests = self.env["maintenance.request"].search(domain)

        # Aggregate per equipment.
        by_equip = {}
        no_equip = {"requests": 0, "sla_breach": 0, "labor": 0.0, "parts": 0.0, "total": 0.0}
        for req in requests:
            bucket = (
                by_equip.setdefault(
                    req.equipment_id.id,
                    {
                        "equipment": req.equipment_id,
                        "requests": 0,
                        "sla_breach": 0,
                        "labor": 0.0,
                        "parts": 0.0,
                        "total": 0.0,
                    },
                )
                if req.equipment_id
                else no_equip
            )
            bucket["requests"] += 1
            if req.x_sla_status == "breach":
                bucket["sla_breach"] += 1
            bucket["labor"] += req.x_labor_cost or 0.0
            bucket["parts"] += req.x_parts_cost or 0.0
            bucket["total"] += req.x_total_cost or 0.0

        lines = []
        g = {"requests": 0, "sla_breach": 0, "labor": 0.0, "parts": 0.0, "total": 0.0}

        def _emit(equip, b):
            equipment = equip
            lines.append(
                {
                    "equipment": equipment.display_name if equipment else "(no equipment)",
                    "category": equipment.category_id.name if equipment and equipment.category_id else "",
                    "requests": b["requests"],
                    "mtbf": equipment.x_mtbf_hours if equipment else 0.0,
                    "mttr": equipment.x_mttr_hours if equipment else 0.0,
                    "failures": equipment.x_total_failures if equipment else 0,
                    "last_failure": equipment.x_last_failure_at.strftime("%d-%b-%Y")
                    if equipment and equipment.x_last_failure_at
                    else "",
                    "sla_breach": b["sla_breach"],
                    "labor": b["labor"],
                    "parts": b["parts"],
                    "total": b["total"],
                }
            )
            for key in g:
                g[key] += b[key]

        for _equip_id, b in by_equip.items():
            _emit(b["equipment"], b)
        if no_equip["requests"]:
            _emit(self.env["maintenance.equipment"], no_equip)

        lines.sort(key=lambda r: r["equipment"])
        lines.append(
            {
                "type": "grand_total",
                "equipment": "Grand Total",
                "requests": g["requests"],
                "sla_breach": g["sla_breach"],
                "labor": g["labor"],
                "parts": g["parts"],
                "total": g["total"],
            }
        )
        return lines
