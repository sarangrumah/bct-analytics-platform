# -*- coding: utf-8 -*-
"""Perjalanan Dinas settlement — read-only mirror of HRIS travel.

Per the agreed scope HRIS owns the travel request/realization. Odoo only
mirrors the travel record (synced by ``custom_finance_portal_sap`` via the HRIS
bridge) and lets Finance *settle* it against a Cash Advance or Reimbursement so
the money side stays in the portal. No approval workflow here — the travel
itself is approved in HRIS.
"""

from __future__ import annotations

from odoo import api, fields, models


class FinanceTravelSettlement(models.Model):
    _name = "finance.travel.settlement"
    _inherit = ["mail.thread", "pdp.audited.mixin"]
    _description = "Perjalanan Dinas Settlement (HRIS mirror)"
    _order = "create_date desc"

    name = fields.Char(string="Reference", required=True, copy=False, index=True, tracking=True)
    x_sap_external_id = fields.Char(string="HRIS Travel ID", index=True, copy=False)
    requester_id = fields.Many2one("hr.employee", string="Employee", tracking=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    currency_id = fields.Many2one("res.currency", default=lambda self: self.env.company.currency_id)
    division_id = fields.Many2one("finance.vertical", string="Division")

    travel_from = fields.Date(string="From")
    travel_to = fields.Date(string="To")
    destination = fields.Char()
    purpose = fields.Text()

    hris_state = fields.Selection(
        selection=[
            ("requested", "Requested"),
            ("approved", "Approved (HRIS)"),
            ("realized", "Realized (HRIS)"),
            ("closed", "Closed"),
        ],
        string="HRIS Status",
        readonly=True,
    )
    estimated_amount = fields.Monetary(string="Estimated", currency_field="currency_id")
    realized_amount = fields.Monetary(string="Realized", currency_field="currency_id")

    # Settlement against portal money documents.
    cash_advance_id = fields.Many2one(
        "finance.cash.advance",
        string="Settled via Cash Advance",
        domain="[('ca_type', '=', 'travel')]",
    )
    reimbursement_id = fields.Many2one("finance.reimbursement", string="Settled via Reimbursement")
    settlement_state = fields.Selection(
        selection=[
            ("unsettled", "Unsettled"),
            ("partial", "Partially Settled"),
            ("settled", "Settled"),
        ],
        default="unsettled",
        compute="_compute_settlement_state",
        store=True,
    )

    _hris_travel_id_uniq = models.Constraint(
        "unique(x_sap_external_id)",
        "HRIS travel id must be unique.",
    )

    @api.depends("cash_advance_id.state", "reimbursement_id.state", "realized_amount")
    def _compute_settlement_state(self):
        for rec in self:
            settled = False
            partial = False
            for doc in (rec.cash_advance_id, rec.reimbursement_id):
                if doc and doc.state in ("approved", "pushed", "posted", "paid"):
                    settled = True
                elif doc:
                    partial = True
            rec.settlement_state = "settled" if settled else ("partial" if partial else "unsettled")
