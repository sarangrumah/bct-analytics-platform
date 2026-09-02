# -*- coding: utf-8 -*-
"""Replenishment proposals — computed in Odoo, approved by a human, pushed to ESB.

The gate is the point. A cron computes what each outlet should order and leaves
it as a **draft in Odoo**; nothing reaches ESB until someone presses Approve.
Automatic ordering against a forecast is how a bad week of data turns into a
warehouse full of chicken.

The quantity is the textbook periodic-review formula::

    need = forecast(lead time + review period) + safety stock - on hand - on order

with two deliberate refusals:

- **on hand unknown → skip the line.** ``qty_for`` returns ``None`` when ESB
  reported no movement for that product in the snapshot window. Treating that as
  zero would order a full cover for something the outlet may already be holding.
- **no forecast → no proposal.** A product with no consumption history has no
  business being auto-ordered.

``on order`` nets off quantities Odoo has already pushed and ESB has not yet
finished. Purchase orders raised by humans directly in ESB are *not* netted —
ESB's index endpoints return document totals, not line quantities, so knowing
them would cost one View call per open document per run. This is the known
limitation of v1; ``review_period_days`` should be set no shorter than the
outlet's actual ordering rhythm so the window does not overlap itself.
"""

from __future__ import annotations

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .fnb_replenishment_rule import REQUEST_PROCESS_PURCHASE, TARGET_DOCS

_logger = logging.getLogger(__name__)

STATES = [
    ("draft", "Draft"),
    ("to_approve", "To Approve"),
    ("approved", "Approved"),
    ("pushed", "Pushed to ESB"),
    ("done", "Done"),
    ("cancelled", "Cancelled"),
]

SKIP_REASONS = [
    ("no_forecast", "No demand forecast"),
    ("unknown_on_hand", "On-hand unknown in ESB"),
    ("sufficient", "Stock already sufficient"),
    ("no_product_detail", "Product not mirrored from ESB"),
]

#: ESB categoryTypeID on a goods transfer request: 1 Goods & Services, 3 Asset.
CATEGORY_TYPE_GOODS = 1


class FnbReplenishmentProposal(models.Model):
    _name = "custom.fnb.replenishment.proposal"
    _description = "F&B Replenishment Proposal"
    _inherit = ["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, copy=False, default=lambda s: s.env._("New"), readonly=True)
    branch_id = fields.Many2one("custom.esb.branch", required=True, ondelete="cascade", index=True, tracking=True)
    company_id = fields.Many2one(related="branch_id.company_id", store=True, index=True)
    target_doc = fields.Selection(
        selection=lambda self: self.env["custom.fnb.replenishment.rule"]._fields["target_doc"].selection,
        required=True,
        tracking=True,
    )
    supplier_id = fields.Many2one("custom.esb.supplier", string="ESB Supplier", tracking=True)
    source_branch_id = fields.Many2one("custom.esb.branch", string="Source Branch", tracking=True)
    source_location_id = fields.Many2one("custom.esb.location", string="Source Location")
    required_date = fields.Date(required=True, default=lambda s: fields.Date.context_today(s), tracking=True)

    state = fields.Selection(STATES, default="draft", required=True, index=True, tracking=True)
    line_ids = fields.One2many("custom.fnb.replenishment.proposal.line", "proposal_id")
    line_count = fields.Integer(compute="_compute_totals", store=True)
    total_qty = fields.Float(compute="_compute_totals", store=True, digits=(20, 4))
    estimated_value = fields.Float(compute="_compute_totals", store=True, digits="Product Price")
    has_unreliable_forecast = fields.Boolean(compute="_compute_totals", store=True)

    esb_outbox_id = fields.Many2one("custom.esb.outbox", string="ESB Document", readonly=True, copy=False)
    esb_doc_num = fields.Char(related="esb_outbox_id.esb_doc_num", string="ESB Document No.", store=True)
    esb_state = fields.Selection(related="esb_outbox_id.state", string="ESB Status")

    approved_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    approved_at = fields.Datetime(readonly=True, copy=False)
    notes = fields.Text()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("custom.fnb.replenishment.proposal") or _(
                    "REPL/NEW"
                )
        return super().create(vals_list)

    @api.depends("line_ids.qty", "line_ids.unit_price", "line_ids.forecast_reliable")
    def _compute_totals(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)
            rec.total_qty = sum(rec.line_ids.mapped("qty"))
            rec.estimated_value = sum(line.qty * (line.unit_price or 0.0) for line in rec.line_ids)
            rec.has_unreliable_forecast = any(not line.forecast_reliable for line in rec.line_ids)

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    @api.model
    def _cron_generate(self):
        sync = self.env["custom.esb.master.sync"]
        log = self.env["custom.esb.sync.log"]
        if not sync._enabled("fnb.replenishment_enabled"):
            log._record("pull", "replenishment", "skipped", message="fnb.replenishment_enabled is off")
            return False
        return self.generate_proposals()

    @api.model
    def generate_proposals(self, rules=None):
        """Evaluate rules and create draft proposals, grouped per ESB document.

        Grouping key is (branch, target doc, supplier or source branch) — one ESB
        document per group, so an outlet ordering from two suppliers gets two
        purchase orders rather than one impossible mixed document.
        """
        Rule = self.env["custom.fnb.replenishment.rule"]
        rules = rules or Rule.search([])
        today = fields.Date.context_today(self)
        grouped = {}
        skipped = []
        for rule in rules:
            line_vals, skip = self._evaluate_rule(rule)
            if skip:
                skipped.append((rule, skip))
                continue
            key = (
                rule.branch_id.id,
                rule.target_doc,
                rule.supplier_id.id,
                rule.source_branch_id.id,
                rule.source_location_id.id,
            )
            grouped.setdefault(key, []).append(line_vals)
            rule.last_run_at = fields.Datetime.now()

        proposals = self.browse()
        for (branch_id, target_doc, supplier_id, source_branch_id, source_location_id), lines in grouped.items():
            # Required date = the longest lead time in the group; the document
            # is only fully useful once its slowest line can arrive.
            lead = max((line["lead_time_days"] for line in lines), default=0)
            proposals |= self.create(
                {
                    "branch_id": branch_id,
                    "target_doc": target_doc,
                    "supplier_id": supplier_id or False,
                    "source_branch_id": source_branch_id or False,
                    "source_location_id": source_location_id or False,
                    "required_date": fields.Date.add(today, days=lead),
                    "line_ids": [(0, 0, line) for line in lines],
                }
            )

        self.env["custom.esb.sync.log"]._record(
            "pull",
            "replenishment",
            "ok",
            record_count=len(rules),
            created_count=len(proposals),
            message=_("%s rule(s) skipped") % len(skipped) if skipped else False,
        )
        return proposals

    @api.model
    def _evaluate_rule(self, rule):
        """Return ``(line_vals, skip_reason)``. Exactly one of them is set."""
        Snapshot = self.env["custom.esb.stock.snapshot"]
        Forecast = self.env["custom.fnb.demand.forecast"]

        if not rule.product_id.x_esb_product_detail_id:
            return None, "no_product_detail"

        forecast = Forecast.sudo().search(
            [("branch_id", "=", rule.branch_id.id), ("product_id", "=", rule.product_id.id)], limit=1
        )
        if not forecast or not forecast.computed_at:
            return None, "no_forecast"

        on_hand = self._on_hand(rule, Snapshot)
        if on_hand is None:
            # Unknown, not zero. See the module docstring.
            return None, "unknown_on_hand"

        cover_days = rule.cover_days
        demand = forecast.horizon_qty(cover_days)
        safety = forecast.safety_stock(rule.lead_time_days, rule.service_level)
        on_order = self._on_order(rule)
        target = demand + safety
        if rule.min_qty:
            target = max(target, rule.min_qty)
        raw_need = target - on_hand - on_order
        qty = rule.round_qty(raw_need)
        if qty <= 0:
            return None, "sufficient"

        return {
            "product_id": rule.product_id.id,
            "rule_id": rule.id,
            "qty": qty,
            "raw_need": raw_need,
            "forecast_daily_qty": forecast.daily_qty,
            "forecast_horizon_qty": demand,
            "safety_stock": safety,
            "on_hand_qty": on_hand,
            "on_order_qty": on_order,
            "cover_days": cover_days,
            "forecast_reliable": forecast.reliable,
            "unit_price": rule.unit_price or self._default_price(rule.product_id),
            "lead_time_days": rule.lead_time_days,
        }, None

    @api.model
    def _on_hand(self, rule, Snapshot):
        """Snapshot quantity, or ``None`` when ESB has not reported this product.

        With a location on the rule the answer is that location's balance; without
        one it is the branch total, which is only meaningful if at least one
        location reported.
        """
        if rule.esb_location_id:
            return Snapshot.qty_for(rule.esb_location_id, rule.product_id)
        snaps = Snapshot.sudo().search([("branch_id", "=", rule.branch_id.id), ("product_id", "=", rule.product_id.id)])
        if not snaps:
            return None
        return sum(snaps.mapped("qty"))

    @api.model
    def _on_order(self, rule):
        """Quantity already on its way from proposals Odoo pushed but ESB has not closed."""
        lines = (
            self.env["custom.fnb.replenishment.proposal.line"]
            .sudo()
            .search(
                [
                    ("product_id", "=", rule.product_id.id),
                    ("proposal_id.branch_id", "=", rule.branch_id.id),
                    ("proposal_id.state", "in", ("approved", "pushed")),
                ]
            )
        )
        return sum(lines.mapped("qty"))

    @api.model
    def _default_price(self, product):
        detail = product.x_esb_detail_ids.filtered("is_purchase_unit")[:1] or product.x_esb_detail_ids[:1]
        return detail.base_price or product.standard_price or 0.0

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------

    def action_submit(self):
        for rec in self.filtered(lambda p: p.state == "draft"):
            if not rec.line_ids:
                raise UserError(_("Proposal %s has no lines to submit.") % rec.display_name)
            rec.state = "to_approve"
        return True

    def action_approve(self):
        """The gate. Approving is what authorises the push to ESB."""
        for rec in self.filtered(lambda p: p.state in ("draft", "to_approve")):
            if not rec.line_ids:
                raise UserError(_("Proposal %s has no lines to approve.") % rec.display_name)
            rec.write(
                {
                    "state": "approved",
                    "approved_by_id": self.env.user.id,
                    "approved_at": fields.Datetime.now(),
                }
            )
            rec._push_to_esb()
        return True

    def action_cancel(self):
        for rec in self.filtered(lambda p: p.state not in ("pushed", "done")):
            rec.state = "cancelled"
        return True

    def action_reset_to_draft(self):
        for rec in self.filtered(lambda p: p.state in ("to_approve", "cancelled")):
            rec.write({"state": "draft", "approved_by_id": False, "approved_at": False})
        return True

    # ------------------------------------------------------------------
    # Push
    # ------------------------------------------------------------------

    def _push_to_esb(self):
        self.ensure_one()
        payload = self._esb_payload()
        outbox = self.env["custom.esb.outbox"].enqueue(self.target_doc, payload, res_model=self._name, res_id=self.id)
        self.write({"esb_outbox_id": outbox.id, "state": "pushed"})
        self.message_post(
            # dict() on the field's selection would fail: it is a callable here.
            body=_("Replenishment pushed to ESB as %(doc)s with %(n)s line(s).")
            % {"doc": dict(TARGET_DOCS).get(self.target_doc), "n": len(self.line_ids)}
        )
        return outbox

    def _esb_payload(self):
        self.ensure_one()
        builder = {
            "purchase_request": self._payload_purchase_request,
            "goods_transfer_request": self._payload_goods_transfer_request,
            "purchase_order": self._payload_purchase_order,
        }[self.target_doc]
        return builder()

    def _payload_purchase_request(self):
        self.ensure_one()
        return {
            "branchID": self.branch_id.esb_branch_id,
            "purchaseRequestDate": fields.Date.to_string(fields.Date.context_today(self)),
            "requiredDate": fields.Date.to_string(self.required_date),
            "isTemplate": False,
            "requestTemplateID": None,
            "additionalInfo": _("Odoo replenishment %s") % self.name,
            "purchaseRequestDetails": [
                {
                    "productDetailID": line.esb_detail_id("stock"),
                    "requestProcessID": line.rule_id.request_process_id() if line.rule_id else REQUEST_PROCESS_PURCHASE,
                    "qty": line.qty,
                    "notes": line.note or "",
                }
                for line in self.line_ids
            ],
        }

    def _payload_goods_transfer_request(self):
        self.ensure_one()
        if not self.source_branch_id:
            raise UserError(_("Proposal %s has no source branch to transfer from.") % self.display_name)
        return {
            "originBranchID": self.source_branch_id.esb_branch_id,
            "destinationBranchID": self.branch_id.esb_branch_id,
            "transferDate": fields.Date.to_string(fields.Date.context_today(self)),
            "categoryTypeID": CATEGORY_TYPE_GOODS,
            "originLocationID": self.source_location_id.esb_location_id or None,
            "purchaseRequestNum": None,
            "additionalInfo": _("Odoo replenishment %s") % self.name,
            "transferDetails": [
                {
                    "productDetailID": line.esb_detail_id("transfer"),
                    "qty": line.qty,
                    # 0 when the transfer is not linked to a purchase request.
                    "requestQty": 0,
                }
                for line in self.line_ids
            ],
        }

    def _payload_purchase_order(self):
        self.ensure_one()
        if not self.supplier_id:
            raise UserError(_("Proposal %s has no ESB supplier to order from.") % self.display_name)
        missing = self.line_ids.filtered(lambda l: not l.unit_price)
        if missing:
            raise UserError(
                _(
                    "ESB requires a price on every purchase-order line. These have none: %s. "
                    "Set a price on the line or on the rule, or raise a Purchase Request instead "
                    "and let ESB price it."
                )
                % ", ".join(missing.mapped("product_id.display_name"))
            )
        return {
            "branchID": self.branch_id.esb_branch_id,
            "purchaseDate": fields.Date.to_string(fields.Date.context_today(self)),
            "requiredDate": fields.Date.to_string(self.required_date),
            "currencyID": self._esb_currency_id(),
            "rate": 1,
            "supplierID": self.supplier_id.esb_supplier_id,
            "flagImportDoc": 0,
            "dueDay": self.supplier_id.due_days or 0,
            "additionalInfo": _("Odoo replenishment %s") % self.name,
            "purchaseDetails": [
                {
                    # Capital P: ESB's own spelling on this endpoint only.
                    "ProductDetailID": line.esb_detail_id("purchase"),
                    "qty": line.qty,
                    "price": line.unit_price,
                    "notes": line.note or "",
                }
                for line in self.line_ids
            ],
        }

    @api.model
    def _esb_currency_id(self):
        raw = self.env["ir.config_parameter"].sudo().get_param("fnb.esb_currency_id", "1")
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 1

    # ------------------------------------------------------------------

    @api.model
    def _cron_close_finished(self):
        """Mark proposals done once ESB has authorized their document."""
        pushed = self.sudo().search([("state", "=", "pushed")])
        done = pushed.filtered(lambda p: p.esb_outbox_id.state == "confirmed")
        done.write({"state": "done"})
        return True


class FnbReplenishmentProposalLine(models.Model):
    _name = "custom.fnb.replenishment.proposal.line"
    _description = "F&B Replenishment Proposal Line"
    _order = "proposal_id, product_id"
    _rec_name = "product_id"

    proposal_id = fields.Many2one("custom.fnb.replenishment.proposal", required=True, ondelete="cascade", index=True)
    rule_id = fields.Many2one("custom.fnb.replenishment.rule", ondelete="set null", index=True)
    product_id = fields.Many2one("product.product", required=True, ondelete="cascade", index=True)
    qty = fields.Float(required=True, digits=(20, 4), help="Quantity to order, after rounding.")
    unit_price = fields.Float(digits="Product Price")
    note = fields.Char()

    # --- the arithmetic, kept visible so a planner can audit the number ---
    raw_need = fields.Float(digits=(20, 4), readonly=True, help="Before rounding and min/max.")
    forecast_daily_qty = fields.Float(digits=(20, 4), readonly=True)
    forecast_horizon_qty = fields.Float(digits=(20, 4), readonly=True, help="Forecast demand over the cover period.")
    safety_stock = fields.Float(digits=(20, 4), readonly=True)
    on_hand_qty = fields.Float(digits=(20, 4), readonly=True)
    on_order_qty = fields.Float(digits=(20, 4), readonly=True)
    cover_days = fields.Integer(readonly=True, help="Lead time + review period.")
    forecast_reliable = fields.Boolean(readonly=True)
    lead_time_days = fields.Integer(readonly=True)

    def esb_detail_id(self, kind):
        self.ensure_one()
        return self.product_id._esb_detail_id(kind)
