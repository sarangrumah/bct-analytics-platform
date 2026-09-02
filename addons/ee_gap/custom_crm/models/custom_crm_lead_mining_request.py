# -*- coding: utf-8 -*-
"""Lead Mining Request (stub) — EE-equivalent feature.

Provides a draft/done state machine and a mocked IAP credits flow. Generates
draft crm.lead records from an internal seed list when ``action_get_leads`` is
called. The real EE feature relies on Odoo IAP, which is outside CE scope, so
the implementation here is intentionally a stub.
"""

from __future__ import annotations

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Internal mock catalogue used when no real IAP service is configured.
_MOCK_COMPANIES = [
    ("Andalas Logistik", "ops@andalaslog.id", "+6281100000001"),
    ("Bumi Sentosa Ritel", "halo@bumisentosa.id", "+6281100000002"),
    ("Cakrawala Digital", "sales@cakrawala.io", "+6281100000003"),
    ("Dwi Tunggal Manufaktur", "office@dwitunggal.co.id", "+6281100000004"),
    ("Eka Karya Konstruksi", "info@ekakarya.co.id", "+6281100000005"),
]


class CustomCrmLeadMiningRequest(models.Model):
    _name = "custom.crm.lead.mining.request"
    _description = "Lead Mining Request (Stub)"
    _order = "create_date desc, id desc"

    name = fields.Char(
        string="Reference",
        default=lambda self: self.env._("New"),
        required=True,
        copy=False,
        readonly=True,
    )
    industry = fields.Char(string="Industry")
    country_id = fields.Many2one("res.country", string="Country")
    employees_range = fields.Selection(
        selection=[
            ("1_10", "1-10"),
            ("11_50", "11-50"),
            ("51_200", "51-200"),
            ("201_500", "201-500"),
            ("500_plus", "500+"),
        ],
        string="Employees",
        default="11_50",
    )
    lead_number = fields.Integer(
        string="Leads to Generate",
        default=3,
        help="How many draft crm.lead records to create on Generate.",
    )

    credits_used = fields.Integer(
        string="Credits Used (Mock)",
        readonly=True,
        default=0,
        help="Mock counter — the real EE feature consumes IAP credits.",
    )

    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("done", "Done"),
        ],
        default="draft",
        required=True,
        readonly=True,
    )

    generated_lead_ids = fields.One2many(
        "crm.lead",
        "x_lead_mining_request_id",
        string="Generated Leads",
        readonly=True,
    )
    generated_lead_count = fields.Integer(
        string="# Generated",
        compute="_compute_generated_lead_count",
    )

    def _compute_generated_lead_count(self):
        for rec in self:
            rec.generated_lead_count = len(rec.generated_lead_ids)

    # ---------- ORM ----------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("custom.crm.lead.mining.request") or _(
                    "LM/%s"
                ) % fields.Datetime.now().strftime("%Y%m%d%H%M%S")
        return super().create(vals_list)

    # ---------- actions ----------

    def action_get_lead_count(self):
        """Returns a mocked estimated count (notification only)."""
        self.ensure_one()
        count = min(len(_MOCK_COMPANIES), max(1, self.lead_number)) * 2
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Lead Mining (Mock)"),
                "message": _("Estimated %d leads match the given criteria.") % count,
                "type": "info",
                "sticky": False,
            },
        }

    def action_get_leads(self):
        """Create up to ``lead_number`` draft crm.lead records from the mock catalogue."""
        self.ensure_one()
        if self.state == "done":
            raise UserError(_("This mining request has already been fulfilled."))
        Lead = self.env["crm.lead"]
        created = self.env["crm.lead"]
        wanted = max(0, min(self.lead_number or 0, len(_MOCK_COMPANIES)))
        for partner_name, email, phone in _MOCK_COMPANIES[:wanted]:
            created |= Lead.create(
                {
                    "name": _("[Mining] %s") % partner_name,
                    "partner_name": partner_name,
                    "email_from": email,
                    "phone": phone,
                    "country_id": self.country_id.id if self.country_id else False,
                    "type": "lead",
                    "x_lead_mining_request_id": self.id,
                }
            )
        self.write(
            {
                "state": "done",
                "credits_used": (self.credits_used or 0) + len(created),
            }
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Generated Leads"),
            "res_model": "crm.lead",
            "view_mode": "list,form",
            "domain": [("id", "in", created.ids)],
        }


class CrmLeadMiningLink(models.Model):
    _inherit = "crm.lead"

    x_lead_mining_request_id = fields.Many2one(
        "custom.crm.lead.mining.request",
        string="Lead Mining Request",
        readonly=True,
        ondelete="set null",
        help="Mining request that generated this lead, if any.",
    )
