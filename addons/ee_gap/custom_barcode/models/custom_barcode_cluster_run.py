# -*- coding: utf-8 -*-
"""Cluster picking — one operator picks for many orders in a single walk.

Strategy:
  * select source pickings;
  * `build_plan()` reads every outstanding move on those pickings, groups them
    by source location, and writes one assignment row per (location, product,
    picking) — already sorted so the operator walks the warehouse in location
    order, with multiple destinations per stop;
  * scans during the walk are recorded into `scan_line_ids`;
  * `action_apply()` calls into the standard scan-session apply per picking.
"""

import json
import logging
from collections import defaultdict

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class CustomBarcodeClusterRun(models.Model):
    _name = "custom.barcode.cluster.run"
    _description = "Barcode Cluster Picking Run"
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
            ("planned", "Planned"),
            ("picking", "Picking"),
            ("done", "Done"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    assignment_ids = fields.One2many(
        "custom.barcode.cluster.assignment",
        "cluster_run_id",
        string="Pick Assignments",
    )
    scan_line_ids = fields.One2many(
        "custom.barcode.scan.line",
        "cluster_run_id",
        string="Scan Lines",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("custom.barcode.cluster.run") or _("Cluster Run")
        return super().create(vals_list)

    # ---------- planning ----------

    def build_plan(self):
        """Group outstanding moves by (location, product, picking) and write
        assignment rows ordered by location name then product name.
        """
        Assignment = self.env["custom.barcode.cluster.assignment"]
        for run in self:
            run.assignment_ids.unlink()
            rows = []
            for picking in run.picking_ids:
                for move in picking.move_ids:
                    if move.state in ("done", "cancel"):
                        continue
                    rows.append(
                        {
                            "cluster_run_id": run.id,
                            "location_id": move.location_id.id,
                            "product_id": move.product_id.id,
                            "picking_id": picking.id,
                            "expected_qty": move.product_uom_qty,
                        }
                    )
            # Stable-sort: location → product → picking name.
            rows.sort(
                key=lambda r: (
                    self.env["stock.location"].browse(r["location_id"]).complete_name or "",
                    self.env["product.product"].browse(r["product_id"]).display_name or "",
                    self.env["stock.picking"].browse(r["picking_id"]).name or "",
                )
            )
            if rows:
                Assignment.create(rows)
                run.state = "planned"
        return True

    def action_start_picking(self):
        for rec in self:
            rec.state = "picking"
        return True

    def action_cancel(self):
        for rec in self:
            rec.state = "cancelled"
        return True

    # ---------- barcode ----------

    def on_barcode_scanned(self, barcode):
        self.ensure_one()
        if self.state in ("draft", "planned"):
            self.state = "picking"

        Session = self.env["custom.barcode.scan.session"]
        gs1 = Session.parse_gs1(barcode)
        product = self.env["product.product"]
        lot = self.env["stock.lot"]
        status = "ok"

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

        # Pick the first assignment row for that product that still has demand.
        picking_id = False
        if product:
            assignment = self.assignment_ids.filtered(
                lambda a: a.product_id.id == product.id and a.scanned_qty < a.expected_qty
            )[:1]
            if assignment:
                take = min(
                    float(gs1.get("weight") or 1.0),
                    assignment.expected_qty - assignment.scanned_qty,
                )
                assignment.scanned_qty += take
                picking_id = assignment.picking_id.id

        qty = float(gs1.get("weight") or 1.0)
        self.env["custom.barcode.scan.line"].create(
            {
                "cluster_run_id": self.id,
                "product_id": product.id if product else False,
                "lot_id": lot.id if lot else False,
                "picking_id": picking_id,
                "raw_barcode": barcode,
                "quantity": qty,
                "status": status if picking_id else ("unallocated" if status == "ok" else status),
                "x_gs1_parsed": json.dumps(gs1) if gs1 else False,
            }
        )
        return True

    # ---------- apply ----------

    def action_apply(self):
        """Apply scan lines to their assigned pickings using the standard session."""
        self.ensure_one()
        Session = self.env["custom.barcode.scan.session"]

        # Group lines by picking.
        by_picking = defaultdict(lambda: self.env["custom.barcode.scan.line"])
        for line in self.scan_line_ids.filtered(lambda l: l.status == "ok" and l.picking_id):
            by_picking[line.picking_id.id] |= line

        applied = 0
        for picking_id, lines in by_picking.items():
            picking = self.env["stock.picking"].browse(picking_id)
            session = Session.create(
                {
                    "picking_id": picking.id,
                    "operator_id": self.operator_id.id,
                    "state": "scanning",
                    "notes": _("Auto-created from cluster %s") % self.name,
                }
            )
            lines.write({"session_id": session.id})
            session.action_apply_to_picking()
            lines.write({"session_id": False})
            applied += 1

        self.state = "done"
        self.message_post(
            body=_("Cluster applied to %s pickings.") % applied,
            subtype_xmlid="mail.mt_note",
        )
        return True


class CustomBarcodeClusterAssignment(models.Model):
    _name = "custom.barcode.cluster.assignment"
    _description = "Cluster Pick Assignment"
    _order = "location_id, product_id"

    cluster_run_id = fields.Many2one(
        "custom.barcode.cluster.run",
        string="Cluster Run",
        required=True,
        ondelete="cascade",
        index=True,
    )
    location_id = fields.Many2one("stock.location", string="Pick From", required=True)
    product_id = fields.Many2one("product.product", string="Product", required=True)
    picking_id = fields.Many2one("stock.picking", string="For Transfer", required=True)
    expected_qty = fields.Float(string="Expected", default=0.0)
    scanned_qty = fields.Float(string="Scanned", default=0.0)
    remaining_qty = fields.Float(string="Remaining", compute="_compute_remaining")

    @api.depends("expected_qty", "scanned_qty")
    def _compute_remaining(self):
        for rec in self:
            rec.remaining_qty = max(rec.expected_qty - rec.scanned_qty, 0.0)
