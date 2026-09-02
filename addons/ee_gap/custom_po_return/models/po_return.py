# -*- coding: utf-8 -*-
from datetime import datetime

from odoo import _, api, Command, fields, models
from odoo.exceptions import UserError, ValidationError


class CustomPoReturn(models.Model):
    _name = "custom.po.return"
    _description = "PO Return (Return to Vendor)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"

    name = fields.Char(
        string="Reference",
        default=lambda self: self.env._("New"),
        copy=False,
        readonly=True,
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Vendor",
        required=True,
        tracking=True,
    )
    date = fields.Date(
        default=fields.Date.context_today,
        required=True,
        tracking=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        default=lambda self: self.env.company,
        required=True,
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        compute="_compute_currency_id",
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("allocated", "Allocated"),
            ("done", "Done"),
            ("cancel", "Cancelled"),
        ],
        default="draft",
        tracking=True,
        copy=False,
    )
    line_ids = fields.One2many(
        comodel_name="custom.po.return.line",
        inverse_name="return_id",
        string="Lines",
        copy=True,
    )
    allocation_ids = fields.One2many(
        comodel_name="custom.po.return.allocation",
        inverse_name="return_id",
        string="Allocations",
        copy=False,
    )
    amount_total = fields.Monetary(
        compute="_compute_amount_total",
        currency_field="currency_id",
    )
    return_picking_ids = fields.Many2many(
        comodel_name="stock.picking",
        compute="_compute_documents",
        string="Return Pickings",
    )
    return_picking_count = fields.Integer(compute="_compute_documents")
    credit_note_ids = fields.Many2many(
        comodel_name="account.move",
        compute="_compute_documents",
        string="Credit Notes",
    )
    credit_note_count = fields.Integer(compute="_compute_documents")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("custom.po.return") or _("New")
        return super().create(vals_list)

    @api.depends("allocation_ids.currency_id", "company_id")
    def _compute_currency_id(self):
        for rec in self:
            rec.currency_id = rec.allocation_ids[:1].currency_id or rec.company_id.currency_id

    @api.depends("allocation_ids.amount")
    def _compute_amount_total(self):
        for rec in self:
            rec.amount_total = sum(rec.allocation_ids.mapped("amount"))

    @api.depends("allocation_ids.return_picking_id", "allocation_ids.credit_note_id")
    def _compute_documents(self):
        for rec in self:
            pickings = rec.allocation_ids.return_picking_id
            notes = rec.allocation_ids.credit_note_id
            rec.return_picking_ids = pickings
            rec.return_picking_count = len(pickings)
            rec.credit_note_ids = notes
            rec.credit_note_count = len(notes)

    # ------------------------------------------------------------------
    # Returnable quantity
    # ------------------------------------------------------------------
    def _get_move_returnable_qty(self, move, ignore_return=None):
        """Returnable qty of an incoming done move, in the product base UoM.

        Counts native Odoo returns (``returned_move_ids``) plus allocations
        pending on other (or this, unless ignored) PO Return documents.
        """
        returned = 0.0
        for ret in move.returned_move_ids.filtered(lambda m: m.state != "cancel"):
            ret_qty = ret.quantity if ret.state == "done" else ret.product_uom_qty
            returned += ret.product_uom._compute_quantity(ret_qty, move.product_uom)
        available = move.product_uom._compute_quantity(move.quantity - returned, move.product_id.uom_id)
        domain = [
            ("move_id", "=", move.id),
            ("return_id.state", "in", ("draft", "allocated")),
        ]
        if ignore_return:
            domain.append(("return_id", "!=", ignore_return.id))
        pending = self.env["custom.po.return.allocation"].sudo().search(domain)
        return available - sum(pending.mapped("qty"))

    # ------------------------------------------------------------------
    # Allocation (FIFO)
    # ------------------------------------------------------------------
    def action_compute_allocation(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Allocation can only be computed on draft returns."))
            if not rec.line_ids:
                raise UserError(_("Add at least one product line first."))
            rec.allocation_ids.unlink()
            for line in rec.line_ids:
                rec._allocate_line(line)
            currencies = rec.allocation_ids.mapped("currency_id")
            if len(currencies) > 1:
                raise UserError(
                    _(
                        "The allocated purchase orders use different currencies (%s). Split the return per currency.",
                        ", ".join(currencies.mapped("name")),
                    )
                )
            rec.state = "allocated"
        return True

    def _get_candidate_purchase_lines(self, line):
        self.ensure_one()
        pols = self.env["purchase.order.line"].search(
            [
                ("order_id.partner_id", "=", self.partner_id.id),
                ("product_id", "=", line.product_id.id),
                ("company_id", "=", self.company_id.id),
                ("order_id.state", "in", ("purchase", "done")),
                ("qty_received", ">", 0),
            ]
        )
        return pols.sorted(
            key=lambda pol: (
                pol.order_id.date_approve or pol.order_id.date_order or datetime.max,
                pol.order_id.id,
                pol.id,
            )
        )

    def _allocate_line(self, line):
        self.ensure_one()
        Allocation = self.env["custom.po.return.allocation"]
        rounding = line.product_id.uom_id.rounding
        remaining = line.qty_to_return
        returnable_by_po = {}
        for pol in self._get_candidate_purchase_lines(line):
            price_unit = pol.product_uom_id._compute_price(pol.price_unit, line.product_id.uom_id)
            in_moves = pol.move_ids.filtered(
                lambda m: (
                    m.state == "done"
                    and not m._is_purchase_return()
                    and not m.origin_returned_move_id
                    and m.product_id == line.product_id
                )
            ).sorted(key=lambda m: (m.date, m.id))
            for move in in_moves:
                returnable = self._get_move_returnable_qty(move)
                returnable_by_po[pol.order_id] = returnable_by_po.get(pol.order_id, 0.0) + max(returnable, 0.0)
                if line.product_id.uom_id.compare(remaining, 0) <= 0:
                    continue
                take = min(remaining, returnable)
                if line.product_id.uom_id.compare(take, 0) <= 0:
                    continue
                Allocation.create(
                    {
                        "return_id": self.id,
                        "line_id": line.id,
                        "purchase_line_id": pol.id,
                        "move_id": move.id,
                        "qty": take,
                        "price_unit": price_unit,
                        "amount": take * price_unit,
                    }
                )
                remaining -= take
        if line.product_id.uom_id.compare(remaining, 0) > 0:
            breakdown_lines = []
            for po, qty in returnable_by_po.items():
                breakdown_lines.append(_("- %(po)s: %(qty)s returnable", po=po.name, qty=qty))
            breakdown = "\n".join(breakdown_lines) or _("- (no received purchase order found)")
            raise UserError(
                _(
                    "Not enough returnable quantity for %(product)s: requested "
                    "%(requested)s, only %(available)s available.\n%(breakdown)s",
                    product=line.product_id.display_name,
                    requested=line.qty_to_return,
                    available=line.qty_to_return - remaining,
                    breakdown=breakdown,
                )
            )

    # ------------------------------------------------------------------
    # Validation: stock returns + credit notes
    # ------------------------------------------------------------------
    def action_validate(self):
        for rec in self:
            if rec.state != "allocated":
                raise UserError(_("Only allocated returns can be validated."))
            if not rec.allocation_ids:
                raise UserError(_("Nothing to return: no allocations."))
            for alloc in rec.allocation_ids:
                available = rec._get_move_returnable_qty(alloc.move_id, ignore_return=rec)
                if alloc.product_id.uom_id.compare(available, alloc.qty) < 0:
                    raise UserError(
                        _(
                            "Returnable quantity changed for %(po)s / %(product)s "
                            "(now %(available)s, allocated %(qty)s). Reset to "
                            "draft and recompute the allocation.",
                            po=alloc.order_id.name,
                            product=alloc.product_id.display_name,
                            available=available,
                            qty=alloc.qty,
                        )
                    )
            rec._create_return_pickings()
            rec._create_credit_notes()
            rec.state = "done"
            rec.message_post(
                body=_(
                    "Return validated: %(picking_count)s return picking(s) "
                    "created as draft (validate them in Inventory to move the "
                    "stock), %(cn_count)s credit note(s), total %(amount)s.",
                    picking_count=rec.return_picking_count,
                    cn_count=rec.credit_note_count,
                    amount=rec.amount_total,
                )
            )
        return True

    def _create_return_pickings(self):
        self.ensure_one()
        groups = {}
        for alloc in self.allocation_ids:
            groups.setdefault(alloc.move_id.picking_id, []).append(alloc)
        for picking, allocs in groups.items():
            wizard = (
                self.env["stock.return.picking"]
                .with_context(active_id=picking.id, active_model="stock.picking")
                .create({"picking_id": picking.id})
            )
            alloc_by_move = {a.move_id.id: a for a in allocs}
            for wline in wizard.product_return_moves:
                alloc = alloc_by_move.get(wline.move_id.id)
                if alloc:
                    wline.quantity = alloc.product_id.uom_id._compute_quantity(alloc.qty, wline.uom_id)
                    wline.to_refund = True
                else:
                    wline.quantity = 0
            new_picking = wizard._create_return()
            new_picking.origin = _(
                "%(name)s (return of %(picking)s)",
                name=self.name,
                picking=picking.name,
            )
            # The return picking is left in its natural draft/ready state (core
            # already confirmed + reserved it) so the warehouse reviews and
            # validates it manually. We only wire the allocation <-> return move
            # link here; the inventory move (and its valuation journal) posts
            # when the user validates the picking, not at PO Return validation.
            for move in new_picking.move_ids:
                alloc = alloc_by_move.get(move.origin_returned_move_id.id)
                if alloc:
                    alloc.return_move_id = move
                    alloc.return_picking_id = new_picking

    def _create_credit_notes(self):
        self.ensure_one()
        groups = {}
        for alloc in self.allocation_ids:
            bills = alloc.purchase_line_id.invoice_lines.move_id.filtered(
                lambda m: m.move_type == "in_invoice" and m.state == "posted"
            )
            bill = bills.sorted(key=lambda m: (m.invoice_date or m.date, m.id))[:1]
            alloc.source_bill_id = bill
            groups.setdefault(bill, self.env["custom.po.return.allocation"])
            groups[bill] |= alloc
        for bill, allocs in groups.items():
            currency = allocs[0].currency_id
            move_vals = {
                "move_type": "in_refund",
                "partner_id": self.partner_id.id,
                "invoice_date": self.date,
                "invoice_origin": self.name,
                "company_id": self.company_id.id,
                "currency_id": currency.id,
                "reversed_entry_id": bill.id or False,
                "ref": _(
                    "PO Return %(name)s%(bill)s",
                    name=self.name,
                    bill=bill and _(" (bill %s)", bill.name) or "",
                ),
                "invoice_line_ids": [
                    Command.create(
                        {
                            "product_id": alloc.product_id.id,
                            "name": _(
                                "%(name)s - %(po)s",
                                name=self.name,
                                po=alloc.order_id.name,
                            ),
                            "quantity": alloc.qty,
                            "product_uom_id": alloc.product_id.uom_id.id,
                            "price_unit": alloc.price_unit,
                            "purchase_line_id": alloc.purchase_line_id.id,
                            "tax_ids": [Command.set(alloc.purchase_line_id.tax_ids.ids)],
                        }
                    )
                    for alloc in allocs
                ],
            }
            credit_note = self.env["account.move"].with_company(self.company_id).create(move_vals)
            allocs.credit_note_id = credit_note

    # ------------------------------------------------------------------
    # Other state changes
    # ------------------------------------------------------------------
    def action_reset_to_draft(self):
        for rec in self:
            if rec.state not in ("allocated", "cancel"):
                raise UserError(_("Only allocated or cancelled returns can be reset to draft."))
            rec.allocation_ids.unlink()
            rec.state = "draft"
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state == "done":
                raise UserError(
                    _(
                        "A validated return cannot be cancelled: stock and "
                        "credit notes were already created. Reverse those "
                        "documents instead."
                    )
                )
            rec.allocation_ids.unlink()
            rec.state = "cancel"
        return True

    @api.ondelete(at_uninstall=False)
    def _unlink_except_done(self):
        if any(rec.state == "done" for rec in self):
            raise UserError(_("Validated returns cannot be deleted."))

    # ------------------------------------------------------------------
    # Smart buttons
    # ------------------------------------------------------------------
    def action_view_return_pickings(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Return Pickings"),
            "res_model": "stock.picking",
            "view_mode": "list,form",
            "domain": [("id", "in", self.return_picking_ids.ids)],
        }

    def action_view_credit_notes(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Credit Notes"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", self.credit_note_ids.ids)],
            "context": {"default_move_type": "in_refund"},
        }


class CustomPoReturnLine(models.Model):
    _name = "custom.po.return.line"
    _description = "PO Return Line"

    return_id = fields.Many2one(
        comodel_name="custom.po.return",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(related="return_id.company_id", store=True)
    currency_id = fields.Many2one(related="return_id.currency_id")
    product_id = fields.Many2one(
        comodel_name="product.product",
        required=True,
        domain=[("purchase_ok", "=", True), ("is_storable", "=", True)],
    )
    uom_id = fields.Many2one(related="product_id.uom_id", string="Unit")
    qty_to_return = fields.Float(
        string="Quantity to Return",
        digits="Product Unit",
        required=True,
    )
    allocation_ids = fields.One2many(
        comodel_name="custom.po.return.allocation",
        inverse_name="line_id",
    )
    allocated_qty = fields.Float(
        compute="_compute_allocated",
        digits="Product Unit",
    )
    amount_subtotal = fields.Monetary(
        compute="_compute_allocated",
        currency_field="currency_id",
    )

    @api.constrains("qty_to_return")
    def _check_qty_to_return(self):
        for line in self:
            if line.product_id.uom_id.compare(line.qty_to_return, 0) <= 0:
                raise ValidationError(_("Quantity to return must be positive."))

    @api.depends("allocation_ids.qty", "allocation_ids.amount")
    def _compute_allocated(self):
        for line in self:
            line.allocated_qty = sum(line.allocation_ids.mapped("qty"))
            line.amount_subtotal = sum(line.allocation_ids.mapped("amount"))


class CustomPoReturnAllocation(models.Model):
    _name = "custom.po.return.allocation"
    _description = "PO Return Allocation"
    _order = "return_id, line_id, id"

    return_id = fields.Many2one(
        comodel_name="custom.po.return",
        required=True,
        ondelete="cascade",
        index=True,
    )
    line_id = fields.Many2one(
        comodel_name="custom.po.return.line",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(related="return_id.company_id", store=True)
    product_id = fields.Many2one(related="line_id.product_id", store=True)
    purchase_line_id = fields.Many2one(
        comodel_name="purchase.order.line",
        required=True,
        index=True,
        string="Purchase Order Line",
    )
    order_id = fields.Many2one(
        related="purchase_line_id.order_id",
        store=True,
        string="Purchase Order",
    )
    currency_id = fields.Many2one(related="purchase_line_id.order_id.currency_id")
    move_id = fields.Many2one(
        comodel_name="stock.move",
        required=True,
        string="Source Receipt Move",
    )
    picking_id = fields.Many2one(
        related="move_id.picking_id",
        store=True,
        string="Goods Receipt",
    )
    source_bill_id = fields.Many2one(
        comodel_name="account.move",
        string="Vendor Bill",
        copy=False,
    )
    qty = fields.Float(string="Quantity", digits="Product Unit")
    price_unit = fields.Float(digits="Product Price")
    amount = fields.Monetary(currency_field="currency_id")
    return_move_id = fields.Many2one(
        comodel_name="stock.move",
        string="Return Move",
        copy=False,
    )
    return_picking_id = fields.Many2one(
        comodel_name="stock.picking",
        string="Return Picking",
        copy=False,
    )
    credit_note_id = fields.Many2one(
        comodel_name="account.move",
        string="Credit Note",
        copy=False,
    )
