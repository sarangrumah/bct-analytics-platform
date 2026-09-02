# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


STATES = [
    ("draft", "Draft"),
    ("confirmed", "Confirmed"),
    ("picked_up", "Picked Up"),
    ("returned", "Returned"),
    ("cancelled", "Cancelled"),
]


class RentalOrder(models.Model):
    _name = "rental.order"
    _description = "Rental Order"
    _inherit = ["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]
    _order = "create_date desc"

    name = fields.Char(default="New", readonly=True, copy=False)
    partner_id = fields.Many2one("res.partner", required=True, tracking=True)
    asset_id = fields.Many2one(
        "rental.asset",
        tracking=True,
        domain="[('state', '!=', 'retired')]",
        help="Single-serial rental mode. Leave empty and set Product for bulk-by-qty rentals.",
    )
    product_id = fields.Many2one(
        "product.product",
        tracking=True,
        help="Bulk-by-qty rental mode. Leave empty when using Asset (single-serial mode).",
    )
    qty = fields.Integer(
        default=1,
        required=True,
        tracking=True,
        help="Main rental quantity (used to scale fees). Must be 1 in serial mode.",
    )
    loan_qty = fields.Integer(
        default=0,
        tracking=True,
        help="Spare/loan quantity shipped alongside the order. Not invoiced; must be returned in full.",
    )

    pickup_dt = fields.Datetime(required=True, tracking=True)
    return_dt_expected = fields.Datetime(required=True, tracking=True)
    return_dt_actual = fields.Datetime(readonly=True, tracking=True)

    daily_rate = fields.Monetary(currency_field="currency_id", required=True)
    deposit_amount = fields.Monetary(currency_field="currency_id")
    days_planned = fields.Float(compute="_compute_days", store=True)
    days_actual = fields.Float(compute="_compute_days", store=True)

    rental_fee = fields.Monetary(compute="_compute_fees", store=True, currency_field="currency_id")
    late_penalty = fields.Monetary(compute="_compute_fees", store=True, currency_field="currency_id")
    total_due = fields.Monetary(compute="_compute_fees", store=True, currency_field="currency_id")

    currency_id = fields.Many2one("res.currency", default=lambda s: s.env.company.currency_id)
    state = fields.Selection(STATES, default="draft", required=True, tracking=True, index=True)
    notes = fields.Text()
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    # Late-fee accrual
    late_fee_rate = fields.Float(
        string="Late Fee Rate (% / day)",
        default=lambda s: float(
            s.env["ir.config_parameter"].sudo().get_param("custom_rental.default_late_fee_rate", "10.0")
        ),
        help="Percentage of rental subtotal charged per overdue day.",
    )
    late_fee_total = fields.Monetary(
        currency_field="currency_id",
        readonly=True,
        copy=False,
        help="Cumulative late fee accrued by the daily cron.",
    )
    late_fee_line_ids = fields.One2many(
        "custom.rental.late.fee.line",
        "order_id",
        string="Late Fee Accruals",
    )

    # BAST documents (custom_bast module; install will succeed when both modules present)
    bast_pickup_id = fields.Many2one(
        "custom.bast.document",
        string="BAST Pickup",
        copy=False,
        domain="[('kind', '=', 'pickup')]",
    )
    bast_return_id = fields.Many2one(
        "custom.bast.document",
        string="BAST Return",
        copy=False,
        domain="[('kind', '=', 'return')]",
    )

    # E-signature capture (data URL bytes, base64 decoded)
    customer_signature = fields.Binary(string="Customer Signature", copy=False)
    customer_signed_by = fields.Char(copy=False)
    customer_signed_at = fields.Datetime(readonly=True, copy=False)

    # Stock picking integration
    pickup_picking_id = fields.Many2one("stock.picking", copy=False, readonly=True)
    return_picking_id = fields.Many2one("stock.picking", copy=False, readonly=True)

    # Internal asset-loan mode (opt-in). When enabled the pickup/return moves
    # are Internal->Internal (Stock <-> On-Loan) so a serial-tracked fixed
    # asset (e.g. a drone) never leaves the company's own location tree and
    # posts no COGS/valuation journal. Off by default -> standard
    # outgoing/incoming behaviour is unchanged for all other rentals.
    is_internal_loan = fields.Boolean(
        string="Internal Asset Loan",
        tracking=True,
        help="Move the unit via an internal transfer (Stock <-> On-Loan) instead of "
        "an outbound/inbound delivery. Keeps a fixed asset on the company's books "
        "with zero accounting impact. Requires an On-Loan Location.",
    )
    on_loan_location_id = fields.Many2one(
        "stock.location",
        string="On-Loan Location",
        domain="[('usage', '=', 'internal')]",
        help="Internal location the unit sits in while on loan. Used only when Internal Asset Loan is enabled.",
    )

    def _pdp_audit_classification(self):
        return "financial"

    @api.depends("pickup_dt", "return_dt_expected", "return_dt_actual")
    def _compute_days(self):
        for rec in self:
            if rec.pickup_dt and rec.return_dt_expected:
                rec.days_planned = max(
                    1.0,
                    (rec.return_dt_expected - rec.pickup_dt).total_seconds() / 86400.0,
                )
            else:
                rec.days_planned = 0.0
            if rec.pickup_dt and rec.return_dt_actual:
                rec.days_actual = max(
                    1.0,
                    (rec.return_dt_actual - rec.pickup_dt).total_seconds() / 86400.0,
                )
            else:
                rec.days_actual = 0.0

    @api.depends("daily_rate", "days_planned", "days_actual", "qty")
    def _compute_fees(self):
        for rec in self:
            billable_qty = rec.qty or 1
            rec.rental_fee = rec.daily_rate * billable_qty * (rec.days_actual or rec.days_planned)
            late_days = max(0.0, (rec.days_actual or 0) - (rec.days_planned or 0))
            # 50% surcharge on late days (legacy compute, distinct from
            # cron-driven late_fee_total which represents post-due accrual).
            # loan_qty is intentionally excluded from billing.
            rec.late_penalty = rec.daily_rate * billable_qty * late_days * 0.5
            rec.total_due = rec.rental_fee + rec.late_penalty + (rec.late_fee_total or 0.0)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("rental.order") or "RNT-???"
            if vals.get("asset_id") and not vals.get("daily_rate"):
                asset = self.env["rental.asset"].browse(vals["asset_id"])
                vals["daily_rate"] = asset.daily_rate
                if not vals.get("deposit_amount"):
                    vals["deposit_amount"] = asset.deposit_amount
        return super().create(vals_list)

    @api.constrains("asset_id", "product_id", "qty", "loan_qty", "is_internal_loan", "on_loan_location_id")
    def _check_rental_mode(self):
        for rec in self:
            if not rec.asset_id and not rec.product_id:
                raise ValidationError(_("Specify either Asset (serial mode) or Product (bulk mode)."))
            if rec.asset_id and rec.product_id:
                raise ValidationError(_("Set Asset OR Product, not both."))
            if rec.asset_id and rec.qty != 1:
                raise ValidationError(_("Serial mode (Asset) requires qty=1. Use Product for bulk."))
            if rec.qty < 1:
                raise ValidationError(_("qty must be at least 1."))
            if rec.loan_qty < 0:
                raise ValidationError(_("loan_qty cannot be negative."))
            if rec.is_internal_loan and not rec.on_loan_location_id:
                raise ValidationError(_("Internal Asset Loan requires an On-Loan Location."))

    @api.constrains("pickup_dt", "return_dt_expected", "asset_id", "state")
    def _check_overlap(self):
        for rec in self:
            if rec.pickup_dt and rec.return_dt_expected and rec.pickup_dt >= rec.return_dt_expected:
                raise ValidationError(_("Expected return must be after pickup."))
            if rec.state in ("cancelled", "returned"):
                continue
            # Overlap check only applies to single-serial asset bookings.
            # Bulk-by-qty (product_id) rentals rely on standard stock availability.
            if not rec.asset_id:
                continue
            overlap = self.sudo().search(
                [
                    ("asset_id", "=", rec.asset_id.id),
                    ("state", "in", ("confirmed", "picked_up")),
                    ("id", "!=", rec.id),
                    ("pickup_dt", "<", rec.return_dt_expected),
                    ("return_dt_expected", ">", rec.pickup_dt),
                ],
                limit=1,
            )
            if overlap:
                raise ValidationError(
                    _(
                        "Asset %(asset)s is already booked %(start)s -> %(end)s (order %(name)s).",
                        asset=rec.asset_id.name,
                        start=overlap.pickup_dt,
                        end=overlap.return_dt_expected,
                        name=overlap.name,
                    )
                )

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_confirm(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only draft orders can be confirmed."))
            rec.write({"state": "confirmed"})
            rec._pdp_audit_write("rental_confirm", rec.id, None)
            rec._create_stock_picking("outgoing")

    def action_pickup(self):
        for rec in self:
            if rec.state != "confirmed":
                raise UserError(_("Only confirmed orders can be picked up."))
            rec.write({"state": "picked_up"})
            rec.asset_id.sudo().write({"state": "on_rent"})
            rec._pdp_audit_write("rental_pickup", rec.id, None)

    def action_return(self):
        for rec in self:
            if rec.state != "picked_up":
                raise UserError(_("Only picked-up orders can be returned."))
            rec.write({"state": "returned", "return_dt_actual": fields.Datetime.now()})
            rec.asset_id.sudo().write({"state": "available"})
            rec._pdp_audit_write("rental_return", rec.id, {"total_due": float(rec.total_due or 0)})
            rec._create_stock_picking("incoming")

    def action_cancel(self):
        for rec in self:
            if rec.state == "returned":
                raise UserError(_("Cannot cancel a returned order."))
            if rec.state == "picked_up":
                rec.asset_id.sudo().write({"state": "available"})
            rec.write({"state": "cancelled"})
            rec._pdp_audit_write("rental_cancel", rec.id, None)

    # ------------------------------------------------------------------
    # Stock picking integration
    # ------------------------------------------------------------------
    def _stock_integration_enabled(self):
        return self.env["ir.config_parameter"].sudo().get_param("custom_rental.config_stock_integration", "True") in (
            "True",
            "true",
            "1",
            True,
        )

    def _resolve_rental_product(self):
        """Return the product.product backing this rental, regardless of mode."""
        self.ensure_one()
        if self.product_id:
            return self.product_id
        if self.asset_id and self.asset_id.product_id:
            return self.asset_id.product_id
        return self.env["product.product"]

    def _resolve_picking_type_and_locations(self, direction):
        """Return (picking_type, location_src, location_dest) for a pickup/return.

        Standard mode (is_internal_loan=False): keep legacy behaviour — first
        outgoing/incoming picking type in the company, delivering to/from the
        Customer/Supplier location. Untouched for every non-loan rental.

        Internal loan mode (is_internal_loan=True): use an internal picking type
        and move the unit Stock <-> On-Loan so a serial-tracked fixed asset never
        leaves the company's internal tree (no COGS, no valuation journal).
        """
        self.ensure_one()
        empty = self.env["stock.picking.type"]
        if self.is_internal_loan:
            if not self.on_loan_location_id:
                return empty, False, False
            ptype = self.env["stock.picking.type"].search(
                [
                    ("code", "=", "internal"),
                    ("company_id", "in", (False, self.company_id.id)),
                ],
                limit=1,
            )
            if not ptype:
                return empty, False, False
            stock_loc = ptype.default_location_src_id or ptype.warehouse_id.lot_stock_id
            on_loan_loc = self.on_loan_location_id
            if not stock_loc:
                return empty, False, False
            # outgoing (confirm) = Stock -> On-Loan; incoming (return) = On-Loan -> Stock
            if direction == "outgoing":
                return ptype, stock_loc, on_loan_loc
            return ptype, on_loan_loc, stock_loc
        # Legacy standard delivery flow.
        ptype = self.env["stock.picking.type"].search(
            [
                ("code", "=", direction),
                ("company_id", "in", (False, self.company_id.id)),
            ],
            limit=1,
        )
        if not ptype:
            return empty, False, False
        loc_src = ptype.default_location_src_id or ptype.warehouse_id.lot_stock_id
        loc_dst = ptype.default_location_dest_id
        return ptype, loc_src, loc_dst

    def _create_stock_picking(self, direction):
        """direction: 'outgoing' (confirm) or 'incoming' (return).

        Creates one stock.move for the main rental qty, plus a second move
        flagged is_loan=True when loan_qty > 0. Loan moves move the same
        product through the same locations as the main move."""
        self.ensure_one()
        if not self._stock_integration_enabled():
            return False
        product = self._resolve_rental_product()
        if not product:
            return False
        Picking = self.env["stock.picking"]
        ptype, loc_src, loc_dst = self._resolve_picking_type_and_locations(direction)
        if not ptype or not (loc_src and loc_dst):
            return False

        def _move(qty, is_loan):
            name = product.display_name
            if is_loan:
                name = "[LOAN] " + name
            return (
                0,
                0,
                {
                    "name": name,
                    "product_id": product.id,
                    "product_uom_qty": float(qty),
                    "product_uom": product.uom_id.id,
                    "location_id": loc_src.id,
                    "location_dest_id": loc_dst.id,
                    "company_id": self.company_id.id,
                    "is_loan": is_loan,
                },
            )

        moves = [_move(self.qty or 1, False)]
        if self.loan_qty and self.loan_qty > 0:
            moves.append(_move(self.loan_qty, True))

        vals = {
            "picking_type_id": ptype.id,
            "partner_id": self.partner_id.id,
            "location_id": loc_src.id,
            "location_dest_id": loc_dst.id,
            "origin": self.name,
            "company_id": self.company_id.id,
            "move_ids": moves,
        }
        picking = Picking.sudo().create(vals)
        if direction == "outgoing":
            self.pickup_picking_id = picking.id
        else:
            self.return_picking_id = picking.id
        return picking

    def action_validate_loan_return(self):
        """Operator confirms the dispatched units have come back in the return
        picking: loan/cadangan quantity in full, and (for serial-tracked units)
        the exact same serials that went out."""
        for rec in self:
            if rec.loan_qty:
                picking = rec.return_picking_id
                if not picking:
                    raise UserError(_("No return picking on order %s.") % rec.name)
                loan_moves = picking.move_ids.filtered("is_loan")
                done = sum(loan_moves.mapped("quantity"))
                if done < rec.loan_qty:
                    raise UserError(
                        _(
                            "Loan unit shortage on %(name)s: %(done)s returned of %(expected)s expected. "
                            "Resolve via inventory adjustment or pursue claim before closing the order.",
                            name=rec.name,
                            done=done,
                            expected=rec.loan_qty,
                        )
                    )
            rec._check_returned_serials()
        return True

    def _check_returned_serials(self):
        """For serial-tracked units, the exact serials dispatched at pickup must
        come back in the return picking. Catches substituted/missing units that a
        pure quantity check would miss. No-op for non-serial (bulk) products."""
        self.ensure_one()
        product = self._resolve_rental_product()
        if not product or product.tracking != "serial":
            return
        if not (self.pickup_picking_id and self.return_picking_id):
            return
        out_serials = set(self.pickup_picking_id.move_line_ids.lot_id.ids)
        back_serials = set(self.return_picking_id.move_line_ids.lot_id.ids)
        if not out_serials:
            return
        missing = out_serials - back_serials
        extra = back_serials - out_serials
        if missing or extra:
            Lot = self.env["stock.lot"]
            missing_names = ", ".join(Lot.browse(list(missing)).mapped("name")) or "-"
            extra_names = ", ".join(Lot.browse(list(extra)).mapped("name")) or "-"
            raise UserError(
                _(
                    "Serial mismatch on return of %(name)s. Dispatched serials must return "
                    "in full.\nMissing: %(missing)s\nUnexpected: %(extra)s\n"
                    "Resolve via inventory adjustment or claim before closing the order.",
                    name=self.name,
                    missing=missing_names,
                    extra=extra_names,
                )
            )

    # ------------------------------------------------------------------
    # BAST generation
    # ------------------------------------------------------------------
    def _ensure_bast_module(self):
        if "custom.bast.document" not in self.env:
            raise UserError(_("Module 'custom_bast' is not installed. Install it before generating BAST documents."))

    def _bast_lines_vals(self):
        """Build BAST line vals from main + loan quantities. Same product, two
        lines when loan_qty > 0 so the loan unit is explicit on the handover."""
        self.ensure_one()
        product = self._resolve_rental_product()
        if not product:
            return []
        lines = [
            {
                "item_description": product.display_name,
                "product_id": product.id,
                "qty": float(self.qty or 1),
                "uom_id": product.uom_id.id,
                "is_loan": False,
            }
        ]
        if self.loan_qty and self.loan_qty > 0:
            lines.append(
                {
                    "item_description": "[LOAN] " + product.display_name,
                    "product_id": product.id,
                    "qty": float(self.loan_qty),
                    "uom_id": product.uom_id.id,
                    "is_loan": True,
                    "note": "Cadangan / loan unit — must be returned in full.",
                }
            )
        return [(0, 0, v) for v in lines]

    def action_generate_bast_pickup(self):
        for rec in self:
            rec._ensure_bast_module()
            if rec.bast_pickup_id:
                raise UserError(_("BAST Pickup already exists for %s.") % rec.name)
            doc = (
                rec.env["custom.bast.document"]
                .sudo()
                .create(
                    {
                        # name assigned by the custom.bast.document sequence
                        "kind": "pickup",
                        # pickup = company hands the unit over to the customer
                        "party_from_id": rec.company_id.partner_id.id,
                        "party_to_id": rec.partner_id.id,
                        "company_id": rec.company_id.id,
                        "reference": "rental.order,%d" % rec.id,
                        "line_ids": rec._bast_lines_vals(),
                    }
                )
            )
            rec.bast_pickup_id = doc.id
        return True

    def action_generate_bast_return(self):
        for rec in self:
            rec._ensure_bast_module()
            if rec.bast_return_id:
                raise UserError(_("BAST Return already exists for %s.") % rec.name)
            doc = (
                rec.env["custom.bast.document"]
                .sudo()
                .create(
                    {
                        # name assigned by the custom.bast.document sequence
                        "kind": "return",
                        # return = customer hands the unit back to the company
                        "party_from_id": rec.partner_id.id,
                        "party_to_id": rec.company_id.partner_id.id,
                        "company_id": rec.company_id.id,
                        "reference": "rental.order,%d" % rec.id,
                        "line_ids": rec._bast_lines_vals(),
                    }
                )
            )
            rec.bast_return_id = doc.id
        return True

    # ------------------------------------------------------------------
    # Late-fee cron
    # ------------------------------------------------------------------
    @api.model
    def _cron_accrue_late_fees(self):
        """Daily cron: for every picked_up order past expected return, accrue
        late fee = days_overdue * (rate/100) * rental_fee_subtotal."""
        Line = self.env["custom.rental.late.fee.line"]
        today = fields.Date.context_today(self)
        now = fields.Datetime.now()
        overdue = self.search(
            [
                ("state", "=", "picked_up"),
                ("return_dt_expected", "<", now),
            ]
        )
        for rec in overdue:
            if Line.search_count([("order_id", "=", rec.id), ("accrued_on", "=", today)]):
                continue
            delta_days = (now - rec.return_dt_expected).total_seconds() / 86400.0
            if delta_days <= 0:
                continue
            subtotal = rec.rental_fee or (rec.daily_rate * (rec.days_planned or 1.0))
            fee = subtotal * (rec.late_fee_rate or 0.0) / 100.0
            if fee <= 0:
                continue
            Line.sudo().create(
                {
                    "order_id": rec.id,
                    "accrued_on": today,
                    "days_overdue": delta_days,
                    "rate": rec.late_fee_rate or 0.0,
                    "base_amount": subtotal,
                    "fee_amount": fee,
                    "currency_id": rec.currency_id.id,
                    "note": "Auto cron accrual",
                }
            )
            rec.sudo().write({"late_fee_total": (rec.late_fee_total or 0.0) + fee})
            rec.message_post(
                body=_("Late fee accrued: %(fee)s (overdue %(days).1f days).", fee=fee, days=delta_days),
                message_type="notification",
            )
        return True

    # ------------------------------------------------------------------
    # Signature
    # ------------------------------------------------------------------
    def action_capture_signature(self, signature_b64=None, signed_by=None):
        self.ensure_one()
        if not signature_b64:
            raise UserError(_("Empty signature."))
        self.write(
            {
                "customer_signature": signature_b64,
                "customer_signed_by": signed_by or self.env.user.name,
                "customer_signed_at": fields.Datetime.now(),
            }
        )
        return True
