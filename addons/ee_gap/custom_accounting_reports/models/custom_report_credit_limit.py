# -*- coding: utf-8 -*-
"""Credit-limit register for the Finance AR team.

One row per customer carrying a credit limit, showing the limit against the
current outstanding receivable (posted, unpaid/partial customer invoices) and
the resulting headroom / % used. Finance AR uses this to review limits and
payment terms (TOP).

The credit limit is read from ``res.partner.custom_credit_limit``
(``custom_accounting_full``); outstanding is recomputed here from posted
receivable moves so the figure is company-scoped and independent of the
non-stored partner compute.
"""

from odoo import models


class CustomReportCreditLimit(models.AbstractModel):
    _name = "custom.report.credit.limit"
    _inherit = "custom.report.engine"
    _description = "Custom Credit Limit Register"

    _report_code = "credit_limit"
    _report_title = "Credit Limit Report"

    def _xlsx_columns(self):
        return [
            {"header": "Customer", "field": "partner", "kind": "text", "width": 32},
            {"header": "Credit Limit", "field": "limit", "kind": "number", "width": 16},
            {"header": "Outstanding AR", "field": "outstanding", "kind": "number", "width": 16},
            {"header": "Available", "field": "available", "kind": "number", "width": 16},
            {"header": "% Used", "field": "pct_used", "kind": "number", "width": 10},
            {"header": "Over Limit", "field": "over_limit", "kind": "text", "width": 10},
        ]

    def _build_lines(self, filters):
        company_ids = filters["company_ids"]
        only_over = filters.get("only_over_limit", False)

        # Outstanding per partner: posted, unpaid/partial customer invoices,
        # residual signed (refunds already carry a negative residual).
        move_domain = [
            ("company_id", "in", company_ids),
            ("move_type", "in", ("out_invoice", "out_refund")),
            ("state", "=", "posted"),
            ("payment_state", "in", ("not_paid", "partial")),
        ]
        if filters.get("partner_ids"):
            move_domain.append(("partner_id", "in", filters["partner_ids"]))

        outstanding_by_partner = {}
        for move in self.env["account.move"].search(move_domain):
            pid = move.commercial_partner_id.id or move.partner_id.id
            outstanding_by_partner[pid] = outstanding_by_partner.get(pid, 0.0) + move.amount_residual

        partner_domain = [("customer_rank", ">", 0)]
        if filters.get("partner_ids"):
            partner_domain = [("id", "in", filters["partner_ids"])]
        partners = self.env["res.partner"].search(partner_domain)

        rows = []
        for partner in partners:
            limit = partner.custom_credit_limit or 0.0
            outstanding = outstanding_by_partner.get(partner.id, 0.0)
            # Skip customers with neither a limit nor any outstanding balance.
            if not limit and not outstanding:
                continue
            available = limit - outstanding
            over = outstanding > limit and limit > 0.0
            if only_over and not over:
                continue
            pct = (outstanding / limit * 100.0) if limit else 0.0
            rows.append(
                {
                    "partner": partner.display_name or "",
                    "limit": limit,
                    "outstanding": outstanding,
                    "available": available,
                    "pct_used": round(pct, 1),
                    "over_limit": "YES" if over else "",
                    "_sort": partner.display_name or "",
                }
            )

        rows.sort(key=lambda r: r["_sort"])

        lines = []
        g_limit = g_out = g_avail = 0.0
        for r in rows:
            g_limit += r["limit"]
            g_out += r["outstanding"]
            g_avail += r["available"]
            r.pop("_sort", None)
            lines.append(r)

        lines.append(
            {
                "type": "grand_total",
                "partner": "Grand Total",
                "limit": g_limit,
                "outstanding": g_out,
                "available": g_avail,
            }
        )
        return lines
