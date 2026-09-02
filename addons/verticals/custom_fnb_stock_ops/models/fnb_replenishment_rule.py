# -*- coding: utf-8 -*-
"""Replenishment policy per outlet and product.

Shaped after ``custom.to.rule`` in ``custom_wms_to_engine`` (sequence, active,
domain-ish scope, cron cadence) but a separate model: the TO engine moves stock
between Odoo ``stock.location`` records, whereas these rules must land as ESB
documents. Reusing it would mean bending it to a target it was not built for.

A rule answers three questions:

1. **How much cover?** ``lead_time_days + review_period_days``, plus safety stock
   sized from the forecast's own volatility at the requested service level.
2. **What shape of document?** Purchase Request (default — approval and PO stay
   in ESB), Goods Transfer Request (from a central kitchen or hub branch), or a
   direct Purchase Order.
3. **What rounding?** ``order_multiple`` for case/pack sizes, ``min_order_qty``
   for supplier minimums.
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

TARGET_DOCS = [
    ("purchase_request", "ESB Purchase Request"),
    ("goods_transfer_request", "ESB Goods Transfer Request"),
    ("purchase_order", "ESB Purchase Order"),
]

#: ESB requestProcessID on a purchase request line.
REQUEST_PROCESS_ALL = 1
REQUEST_PROCESS_PURCHASE = 2
REQUEST_PROCESS_TRANSFER = 3


class FnbReplenishmentRule(models.Model):
    _name = "custom.fnb.replenishment.rule"
    _description = "F&B Replenishment Rule"
    _inherit = ["mail.thread", "pdp.audited.mixin"]
    _order = "sequence, branch_id, product_id"

    name = fields.Char(compute="_compute_name", store=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    branch_id = fields.Many2one("custom.esb.branch", required=True, ondelete="cascade", index=True, tracking=True)
    product_id = fields.Many2one("product.product", required=True, ondelete="cascade", index=True, tracking=True)
    esb_location_id = fields.Many2one(
        "custom.esb.location",
        string="Stock Location",
        domain="[('branch_id', '=', branch_id)]",
        help="Which location's on-hand counts towards this rule. Leave empty to use the whole branch.",
    )
    company_id = fields.Many2one(related="branch_id.company_id", store=True, index=True)

    # --- policy ---
    lead_time_days = fields.Integer(default=2, required=True, tracking=True, help="Supplier or hub lead time.")
    review_period_days = fields.Integer(
        default=7, required=True, tracking=True, help="How long until the next replenishment run."
    )
    service_level = fields.Integer(
        default=95, required=True, tracking=True, help="Target in-stock probability; drives safety stock."
    )
    min_qty = fields.Float(digits=(20, 4), help="Never let projected stock fall below this, whatever the forecast.")
    max_qty = fields.Float(digits=(20, 4), help="Cap the resulting order. 0 = no cap.")
    order_multiple = fields.Float(default=0.0, digits=(20, 4), help="Round up to a multiple (case/pack size).")
    min_order_qty = fields.Float(default=0.0, digits=(20, 4), help="Supplier minimum order quantity.")

    # --- output ---
    target_doc = fields.Selection(TARGET_DOCS, default="purchase_request", required=True, tracking=True)
    supplier_id = fields.Many2one(
        "custom.esb.supplier", string="ESB Supplier", help="Required for a direct Purchase Order."
    )
    source_branch_id = fields.Many2one(
        "custom.esb.branch", string="Source Branch", help="Required for a Goods Transfer Request."
    )
    source_location_id = fields.Many2one(
        "custom.esb.location", string="Source Location", domain="[('branch_id', '=', source_branch_id)]"
    )
    unit_price = fields.Float(
        digits="Product Price",
        help="Purchase Order line price. Defaults to the ESB product detail's base price when left at zero.",
    )

    last_run_at = fields.Datetime(readonly=True)

    _branch_product_uniq = models.Constraint(
        "unique(branch_id, product_id)", "One replenishment rule per branch/product."
    )

    @api.depends("branch_id", "product_id")
    def _compute_name(self):
        for rec in self:
            rec.name = f"{rec.product_id.display_name} @ {rec.branch_id.display_name}"

    @api.constrains("service_level")
    def _check_service_level(self):
        for rec in self:
            if not 50 <= rec.service_level <= 99:
                raise ValidationError(_("Service level must be between 50 and 99 percent."))

    @api.constrains("target_doc", "supplier_id", "source_branch_id")
    def _check_target_requirements(self):
        for rec in self:
            if rec.target_doc == "purchase_order" and not rec.supplier_id:
                raise ValidationError(
                    _("Rule %s targets a Purchase Order, which ESB requires a supplier for.") % rec.name
                )
            if rec.target_doc == "goods_transfer_request" and not rec.source_branch_id:
                raise ValidationError(
                    _("Rule %s targets a Goods Transfer Request, so it needs a source branch.") % rec.name
                )
            if rec.target_doc == "goods_transfer_request" and rec.source_branch_id == rec.branch_id:
                raise ValidationError(_("Rule %s cannot transfer a branch to itself.") % rec.name)

    # ------------------------------------------------------------------

    @property
    def cover_days(self):
        self.ensure_one()
        return max(1, (self.lead_time_days or 0) + (self.review_period_days or 0))

    def request_process_id(self):
        """ESB requestProcessID for a purchase-request line."""
        self.ensure_one()
        return REQUEST_PROCESS_TRANSFER if self.source_branch_id else REQUEST_PROCESS_PURCHASE

    def round_qty(self, qty):
        """Apply supplier minimum then pack rounding, in that order.

        Rounding after the minimum matters: a minimum of 10 with a pack size of
        4 must give 12, not 10.
        """
        self.ensure_one()
        if qty <= 0:
            return 0.0
        if self.min_order_qty and qty < self.min_order_qty:
            qty = self.min_order_qty
        multiple = self.order_multiple or 0.0
        if multiple > 0:
            qty = multiple * -(-qty // multiple)  # ceil division, float-safe
        if self.max_qty and qty > self.max_qty:
            qty = self.max_qty
        return qty
