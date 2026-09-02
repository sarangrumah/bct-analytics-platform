# -*- coding: utf-8 -*-
"""Maps GL accounts -> emission factors for the auto-collect cron.

When the cron runs, every posted ``account.move.line`` whose ``account_id``
matches a row in this table is converted into a ``custom.esg.measurement``:

    measurement.value = abs(aml.balance / unit_cost) * factor.kg_co2_per_unit

where ``unit_cost`` (optional) lets us turn a money amount (e.g. IDR 1.5M
fuel bill) into an activity quantity (e.g. litres) before applying the
emission factor. If ``unit_cost`` is 0, the raw ``aml.quantity`` is used.
"""

from __future__ import annotations

from odoo import api, fields, models


class CustomEsgAccountMapping(models.Model):
    _name = "custom.esg.account.mapping"
    _description = "ESG Account → Emission Factor Mapping"
    _order = "account_id"

    name = fields.Char(string="Label", compute="_compute_name", store=False)
    account_id = fields.Many2one(
        comodel_name="account.account",
        string="GL Account",
        required=True,
        ondelete="cascade",
    )
    factor_id = fields.Many2one(
        comodel_name="custom.esg.emission.factor",
        string="Emission Factor",
        required=True,
        ondelete="restrict",
    )
    unit_cost = fields.Float(
        string="Unit Cost (Currency / Activity Unit)",
        digits=(16, 4),
        help=("Optional: divide aml.balance by this to derive activity quantity. If 0, use aml.quantity instead."),
    )
    is_active = fields.Boolean(string="Active", default=True)
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        default=lambda self: self.env.company,
    )

    _account_factor_uniq = models.Constraint(
        "unique(account_id, factor_id, company_id)",
        "Same (account, factor) mapping already exists for this company.",
    )

    @api.depends("account_id", "factor_id")
    def _compute_name(self):
        for rec in self:
            rec.name = "%s → %s" % (
                rec.account_id.display_name or "",
                rec.factor_id.name or "",
            )

    @api.model
    def _cron_collect_emission_from_accounting(self):
        """Scan posted account.move.line rows and emit ESG measurements.

        Idempotent: a measurement carrying ``source_document`` of the form
        ``aml:<id>`` is skipped on subsequent runs.
        """
        Aml = self.env["account.move.line"].sudo()
        Measurement = self.env["custom.esg.measurement"].sudo()

        active_maps = self.sudo().search([("is_active", "=", True)])
        if not active_maps:
            return 0

        created = 0
        for mp in active_maps:
            existing_refs = set(
                Measurement.search(
                    [
                        ("source_document", "=like", "aml:%"),
                        ("metric_id", "=", (mp.factor_id.metric_id.id or 0)),
                    ]
                ).mapped("source_document")
            )
            amls = Aml.search(
                [
                    ("account_id", "=", mp.account_id.id),
                    ("parent_state", "=", "posted"),
                ]
            )
            for aml in amls:
                ref = "aml:%d" % aml.id
                if ref in existing_refs:
                    continue
                if mp.unit_cost:
                    activity_qty = abs(aml.balance) / mp.unit_cost
                else:
                    activity_qty = abs(aml.quantity or 0.0)
                value_kg_co2 = activity_qty * mp.factor_id.kg_co2_per_unit
                vals = {
                    "metric_id": mp.factor_id.metric_id.id or False,
                    "period_start": aml.date,
                    "period_end": aml.date,
                    "value": value_kg_co2,
                    "source_document": ref,
                    "notes": ("Auto-collected from account.move.line %d (factor: %s)") % (aml.id, mp.factor_id.name),
                    "company_id": (aml.company_id.id or mp.company_id.id or self.env.company.id),
                    "state": "draft",
                }
                if not vals["metric_id"]:
                    # Skip silently — factor not yet linked to a metric.
                    continue
                Measurement.create(vals)
                created += 1
        return created
