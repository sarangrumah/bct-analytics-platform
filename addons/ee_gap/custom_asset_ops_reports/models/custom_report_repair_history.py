# -*- coding: utf-8 -*-
"""#19 Repair report (monthly) — drone/tool repair history.

One row per ``repair.order`` created in the report period, with SLA status,
rework flag and the labor / material / total cost already computed on the order.
SLA status values are on_track / at_risk / breached / done.
"""

from datetime import datetime, time, timedelta

from odoo import models


class CustomReportRepairHistory(models.AbstractModel):
    _name = "custom.report.repair.history"
    _inherit = "custom.report.engine"
    _description = "Repair History Report"

    _report_code = "repair_history"
    _report_title = "Repair Report"

    def _xlsx_columns(self):
        return [
            {"header": "Repair No", "field": "name", "kind": "text", "width": 18},
            {"header": "Equipment", "field": "equipment", "kind": "text", "width": 28},
            {"header": "Complaint", "field": "complaint", "kind": "text", "width": 34},
            {"header": "State", "field": "state", "kind": "text", "width": 14},
            {"header": "Promised", "field": "promised", "kind": "text", "width": 14},
            {"header": "Actual", "field": "actual", "kind": "text", "width": 14},
            {"header": "SLA", "field": "sla", "kind": "text", "width": 12},
            {"header": "Rework", "field": "rework", "kind": "text", "width": 8},
            {"header": "Labor", "field": "labor", "kind": "number", "width": 14},
            {"header": "Material", "field": "material", "kind": "number", "width": 14},
            {"header": "Total", "field": "total", "kind": "number", "width": 16},
        ]

    def _build_lines(self, filters):
        start = datetime.combine(filters["date_from"], time.min)
        end = datetime.combine(filters["date_to"], time.min) + timedelta(days=1)
        domain = [
            ("create_date", ">=", start),
            ("create_date", "<", end),
        ]
        if filters.get("company_ids"):
            domain.append(("company_id", "in", filters["company_ids"] + [False]))

        Repair = self.env["repair.order"]
        state_labels = dict(Repair._fields["state"]._description_selection(self.env))
        sla_labels = dict(Repair._fields["x_sla_status"]._description_selection(self.env))

        orders = Repair.search(domain, order="create_date, name")
        lines = []
        g_labor = g_mat = g_total = 0.0
        n_rework = 0
        for order in orders:
            g_labor += order.x_labor_cost or 0.0
            g_mat += order.x_material_cost or 0.0
            g_total += order.x_total_repair_cost or 0.0
            if order.x_returned:
                n_rework += 1
            lines.append(
                {
                    "name": order.name or "",
                    "equipment": order.x_equipment_id.display_name or "",
                    "complaint": order.x_id_complaint or "",
                    "state": state_labels.get(order.state, order.state or ""),
                    "promised": order.x_promised_completion_date.strftime("%d-%b-%Y")
                    if order.x_promised_completion_date
                    else "",
                    "actual": order.x_actual_completion_date.strftime("%d-%b-%Y")
                    if order.x_actual_completion_date
                    else "",
                    "sla": sla_labels.get(order.x_sla_status, order.x_sla_status or ""),
                    "rework": "YES" if order.x_returned else "",
                    "labor": order.x_labor_cost or 0.0,
                    "material": order.x_material_cost or 0.0,
                    "total": order.x_total_repair_cost or 0.0,
                }
            )

        lines.append(
            {
                "type": "grand_total",
                "name": "Grand Total",
                "complaint": "Rework: %d" % n_rework,
                "labor": g_labor,
                "material": g_mat,
                "total": g_total,
            }
        )
        return lines
