# -*- coding: utf-8 -*-
"""Auto-spawn an internal asset-loan (rental.order) when the mirrored
intercompany sales order carrying the loan service line is confirmed.

Only the service line is ever invoiced through the SO. The physical asset
(e.g. drone) moves via an Internal->Internal transfer driven by the spawned
rental.order, so it never leaves the selling company's location tree and
posts no COGS/valuation journal.
"""

from __future__ import annotations

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    loan_order_ids = fields.One2many(
        "rental.order",
        "sale_order_id",
        string="Asset Loans",
        copy=False,
    )
    loan_order_count = fields.Integer(compute="_compute_loan_order_count")
    is_asset_loan = fields.Boolean(
        compute="_compute_is_asset_loan",
        help="True when this SO carries the loan service line of its intercompany rule.",
    )

    @api.depends("loan_order_ids")
    def _compute_loan_order_count(self):
        for so in self:
            so.loan_order_count = len(so.loan_order_ids)

    @api.depends("order_line.product_id", "x_custom_ic_rule_id.loan_service_product_id")
    def _compute_is_asset_loan(self):
        for so in self:
            rule = so.x_custom_ic_rule_id
            svc = rule.loan_service_product_id if rule else False
            so.is_asset_loan = bool(svc) and svc in so.order_line.product_id

    # ------------------------------------------------------------------
    def action_confirm(self):
        res = super().action_confirm()
        for so in self:
            rule = so.x_custom_ic_rule_id
            if not (rule and rule.spawn_rental_loan):
                continue
            if not so.is_asset_loan:
                continue
            # Idempotent: one event loan per SO.
            if so.loan_order_ids.filtered(lambda l: l.loan_type == "event"):
                continue
            so._spawn_asset_loan(rule, "event")
        return res

    def action_create_preflight_loan(self):
        """On-demand: not every event has a pre-flight survey cycle."""
        self.ensure_one()
        rule = self.x_custom_ic_rule_id
        if not (rule and rule.spawn_rental_loan and rule.loan_asset_product_id):
            raise UserError(_("This sales order has no intercompany rule configured for asset loans."))
        if self.loan_order_ids.filtered(lambda l: l.loan_type == "preflight"):
            raise UserError(_("A pre-flight loan already exists for %s.") % self.name)
        loan = self._spawn_asset_loan(rule, "preflight")
        return {
            "type": "ir.actions.act_window",
            "res_model": "rental.order",
            "res_id": loan.id,
            "view_mode": "form",
            "target": "current",
        }

    def _spawn_asset_loan(self, rule, loan_type):
        self.ensure_one()
        if not rule.loan_asset_product_id:
            raise UserError(_("Intercompany rule '%s' has no Loan Asset Product configured.") % rule.name)
        if not rule.loan_on_loan_location_id:
            raise UserError(_("Intercompany rule '%s' has no On-Loan Location configured.") % rule.name)
        # Primary qty = qty of the loan service line on this SO.
        svc = rule.loan_service_product_id
        primary_qty = int(sum(self.order_line.filtered(lambda l: l.product_id == svc).mapped("product_uom_qty"))) or 1
        pickup_dt = self.commitment_date or fields.Datetime.now()
        return_dt = pickup_dt + timedelta(days=1)
        loan = self.env["rental.order"].create(
            {
                "partner_id": self.partner_id.id,
                "product_id": rule.loan_asset_product_id.id,
                "qty": primary_qty,
                "loan_qty": 0,
                "is_internal_loan": True,
                "on_loan_location_id": rule.loan_on_loan_location_id.id,
                "pickup_dt": pickup_dt,
                "return_dt_expected": return_dt,
                "daily_rate": 0.0,
                "company_id": self.company_id.id,
                "sale_order_id": self.id,
                "loan_type": loan_type,
                "notes": _("Auto-spawned from %s (%s cycle).") % (self.name, loan_type),
            }
        )
        self.message_post(
            body=_(
                "Asset loan %(loan)s (%(type)s) created for %(qty)s unit(s).",
                loan=loan.name,
                type=loan_type,
                qty=primary_qty,
            )
        )
        return loan

    def action_view_loan_orders(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Asset Loans"),
            "res_model": "rental.order",
            "view_mode": "list,form",
            "domain": [("sale_order_id", "=", self.id)],
        }
