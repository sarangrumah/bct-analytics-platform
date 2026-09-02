# -*- coding: utf-8 -*-
"""Per-tenant per-month usage counters (billing + observability)."""

from __future__ import annotations

from datetime import date

from odoo import api, fields, models


class CoretaxPajakkuUsage(models.Model):
    _name = "custom.coretax.pajakku.usage"
    _description = "Pajakku Usage Counter"
    _order = "period desc, company_id"

    company_id = fields.Many2one("res.company", required=True, index=True)
    period = fields.Date(required=True, index=True, help="First day of the month being tracked.")

    api_calls = fields.Integer(default=0)
    faktur_submits = fields.Integer(default=0)
    bupot_submits = fields.Integer(default=0)
    errors = fields.Integer(default=0)

    last_updated_at = fields.Datetime(default=fields.Datetime.now)

    _uniq_company_period = models.Constraint(
        "unique(company_id, period)",
        "One usage row per company per month.",
    )

    # --------------------------------------------------------------

    @api.model
    def _get_current(self, company=None):
        company = company or self.env.company
        period = date.today().replace(day=1)
        row = self.search([("company_id", "=", company.id), ("period", "=", period)], limit=1)
        if not row:
            row = self.create({"company_id": company.id, "period": period})
        return row

    @api.model
    def increment(self, kind: str, company=None, by: int = 1):
        """Atomically bump a counter. ``kind`` ∈ {api_calls, faktur_submits, bupot_submits, errors}.

        Uses ``cr.execute`` so concurrent payslip + faktur submissions
        don't race on the same row.
        """
        if kind not in ("api_calls", "faktur_submits", "bupot_submits", "errors"):
            return
        row = self._get_current(company)
        # Avoid concurrent stale-read: bump via SQL atomic add
        self.env.cr.execute(
            f"UPDATE custom_coretax_pajakku_usage SET {kind} = {kind} + %s, "
            "last_updated_at = (now() at time zone 'UTC') WHERE id = %s",
            (by, row.id),
        )
