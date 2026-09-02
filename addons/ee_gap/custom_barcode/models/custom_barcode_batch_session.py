# -*- coding: utf-8 -*-
import json
import logging
from collections import defaultdict

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class CustomBarcodeBatchSession(models.Model):
    """Scan once, distribute the scanned quantities across several pickings.

    Workflow:
      * pick the source pickings (`picking_ids`);
      * scan products (via `on_barcode_scanned`) — every scan creates a line
        owned by this batch (no picking yet) with status='unallocated';
      * call `auto_distribute_lines()` — for each product, walk the pickings in
        order and assign portions of the scanned qty to each picking up to its
        outstanding demand, splitting the scan line if necessary;
      * call `action_apply()` — for each picking, hand its allocated lines to a
        transient session and reuse the standard apply-to-picking routine.
    """

    _name = "custom.barcode.batch.session"
    _description = "Barcode Batch Session"
    _inherit = ["mail.thread", "pdp.audited.mixin", "barcodes.barcode_events_mixin"]
    _order = "create_date desc"

    name = fields.Char(
        required=True,
        default="New",
        tracking=True,
        copy=False,
    )
    picking_ids = fields.Many2many(
        "stock.picking",
        string="Transfers",
        domain="[('state', 'in', ('confirmed', 'assigned'))]",
    )
    operator_id = fields.Many2one(
        "res.users",
        string="Operator",
        default=lambda self: self.env.user,
        tracking=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("scanning", "Scanning"),
            ("done", "Done"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    scan_line_ids = fields.One2many(
        "custom.barcode.scan.line",
        "batch_session_id",
        string="Scan Lines",
    )
    line_count = fields.Integer(compute="_compute_line_count")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("custom.barcode.batch.session") or _(
                    "Batch Session"
                )
        return super().create(vals_list)

    @api.depends("scan_line_ids")
    def _compute_line_count(self):
        for rec in self:
            rec.line_count = len(rec.scan_line_ids)

    # ---------- workflow ----------

    def action_start(self):
        for rec in self:
            rec.state = "scanning"
        return True

    def action_cancel(self):
        for rec in self:
            rec.state = "cancelled"
        return True

    # ---------- barcode ----------

    def on_barcode_scanned(self, barcode):
        self.ensure_one()
        if self.state == "draft":
            self.state = "scanning"
        # Reuse the same lookup/parse logic by delegating to scan.session model
        # statically (no record needed for parse_gs1).
        Session = self.env["custom.barcode.scan.session"]
        gs1 = Session.parse_gs1(barcode)
        product = self.env["product.product"]
        lot = self.env["stock.lot"]
        status = "unallocated"

        if gs1.get("gtin"):
            product = self.env["product.product"]._resolve_barcode(gs1["gtin"])
        if not product:
            product = self.env["product.product"]._resolve_barcode(barcode)
        if gs1.get("lot") and product:
            lot = self.env["stock.lot"].search(
                [("name", "=", gs1["lot"]), ("product_id", "=", product.id)],
                limit=1,
            )
        if not product:
            status = "not_found"

        qty = float(gs1.get("weight") or 1.0)
        self.env["custom.barcode.scan.line"].create(
            {
                "batch_session_id": self.id,
                "product_id": product.id if product else False,
                "lot_id": lot.id if lot else False,
                "raw_barcode": barcode,
                "quantity": qty,
                "status": status,
                "x_gs1_parsed": json.dumps(gs1) if gs1 else False,
            }
        )
        return True

    # ---------- distribution ----------

    def auto_distribute_lines(self):
        """Allocate unallocated scan lines to the batch's pickings.

        Greedy by picking-order: for each product, drain its scan-qty into the
        first picking with outstanding demand, then the next, and so on.
        Splits a scan line if it spans two pickings.  Lines that can't be
        placed remain status='unallocated'.
        """
        self.ensure_one()

        # Outstanding demand[picking_id][product_id] = qty
        demand = defaultdict(lambda: defaultdict(float))
        for picking in self.picking_ids:
            for move in picking.move_ids:
                if move.state in ("done", "cancel"):
                    continue
                demand[picking.id][move.product_id.id] += move.product_uom_qty

        total_allocated = 0
        # Group scan lines by product so we can chain across pickings.
        lines_by_product = defaultdict(list)
        for line in self.scan_line_ids.filtered(lambda l: l.status in ("ok", "unallocated") and l.product_id):
            lines_by_product[line.product_id.id].append(line)

        for product_id, lines in lines_by_product.items():
            for line in lines:
                remaining = line.quantity
                if remaining <= 0:
                    continue
                # First-fit by picking order in self.picking_ids.
                for picking in self.picking_ids:
                    if remaining <= 0:
                        break
                    avail = demand[picking.id].get(product_id, 0.0)
                    if avail <= 0:
                        continue
                    take = min(remaining, avail)
                    if take >= remaining:
                        # Whole line fits here.
                        line.write(
                            {
                                "picking_id": picking.id,
                                "status": "ok",
                            }
                        )
                        demand[picking.id][product_id] = avail - take
                        remaining = 0
                        total_allocated += 1
                    else:
                        # Split: original keeps `take`, new line carries the rest.
                        line.copy(
                            {
                                "quantity": remaining - take,
                                "status": "unallocated",
                                "picking_id": False,
                            }
                        )
                        line.write(
                            {
                                "quantity": take,
                                "picking_id": picking.id,
                                "status": "ok",
                            }
                        )
                        demand[picking.id][product_id] = 0.0
                        remaining = 0
                        total_allocated += 1
                if remaining > 0:
                    line.write({"quantity": remaining, "status": "unallocated"})

        self.message_post(
            body=_("Auto-distribute: allocated %s scan lines across %s pickings.")
            % (total_allocated, len(self.picking_ids)),
            subtype_xmlid="mail.mt_note",
        )
        return True

    def action_apply(self):
        """Push the allocated lines into each picking via a transient session."""
        self.ensure_one()
        Session = self.env["custom.barcode.scan.session"]
        applied_pickings = 0

        for picking in self.picking_ids:
            lines = self.scan_line_ids.filtered(lambda l: l.picking_id.id == picking.id and l.status == "ok")
            if not lines:
                continue
            session = Session.create(
                {
                    "picking_id": picking.id,
                    "operator_id": self.operator_id.id,
                    "state": "scanning",
                    "notes": _("Auto-created from batch %s") % self.name,
                }
            )
            # Reparent lines (temporarily) to the new session for the apply pass.
            lines.write({"session_id": session.id})
            session.action_apply_to_picking()
            # Detach so the batch keeps ownership for audit / reporting.
            lines.write({"session_id": False})
            applied_pickings += 1

        self.state = "done"
        self.message_post(
            body=_("Batch applied to %s pickings.") % applied_pickings,
            subtype_xmlid="mail.mt_note",
        )
        return True
