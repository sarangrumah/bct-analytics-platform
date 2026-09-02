# -*- coding: utf-8 -*-
import json
import logging
import re
from datetime import datetime

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


# Subset of GS1 Application Identifiers we care about.  Each entry is:
#   AI -> (key, fixed_length_or_None, parser_callable)
# When fixed length is None the value is taken up to the FNC1 (\x1d) or end.
def _parse_date_yyMMdd(raw):
    # GS1 date format YYMMDD; YY pivots at 50/49 per GS1 spec, but we keep simple.
    try:
        dt = datetime.strptime(raw, "%y%m%d").date()
        return dt.isoformat()
    except ValueError:
        return raw


def _parse_weight_kg(raw):
    # AI 310n / 320n use the last digit of AI as decimal indicator (n).
    # The caller passes the (value, n_decimals) tuple already split.
    value, n = raw
    try:
        return float(value) / (10**n)
    except (ValueError, TypeError):
        return None


_GS1_AI = {
    "01": ("gtin", 14, str),
    "10": ("lot", None, str),  # variable-length, FNC1 terminated
    "17": ("exp_date", 6, _parse_date_yyMMdd),
    "11": ("prod_date", 6, _parse_date_yyMMdd),
    "21": ("serial", None, str),  # variable
    "30": ("count", None, str),  # variable
}
# AIs 310n / 320n: net weight (kg / lb) with n decimals.  GS1 codes these as a
# 4-character AI where the 4th digit is the implied-decimal indicator n, e.g.
# "3103" = weight in kg, 3 decimals; followed by 6 numeric digits.
_GS1_WEIGHT_RE = re.compile(r"^(310|320)(\d)$")


class CustomBarcodeScanSession(models.Model):
    _name = "custom.barcode.scan.session"
    _description = "Barcode Scan Session"
    _inherit = ["mail.thread", "pdp.audited.mixin", "barcodes.barcode_events_mixin"]
    _order = "create_date desc"

    name = fields.Char(
        required=True,
        default="New",
        tracking=True,
        copy=False,
    )
    picking_id = fields.Many2one(
        "stock.picking",
        string="Transfer",
        tracking=True,
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
            ("completed", "Completed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    line_ids = fields.One2many(
        "custom.barcode.scan.line",
        "session_id",
        string="Scan Lines",
    )
    total_scanned = fields.Integer(
        compute="_compute_total_scanned",
        store=True,
    )
    notes = fields.Text()

    # ---------- defaults / naming ----------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("custom.barcode.scan.session") or _("Scan Session")
        return super().create(vals_list)

    # ---------- computes ----------

    @api.depends("line_ids")
    def _compute_total_scanned(self):
        for rec in self:
            rec.total_scanned = len(rec.line_ids)

    # ---------- workflow ----------

    def action_start_scanning(self):
        for rec in self:
            rec.state = "scanning"
        return True

    def action_complete(self):
        for rec in self:
            rec.state = "completed"
        return True

    def action_cancel(self):
        for rec in self:
            rec.state = "cancelled"
        return True

    def action_reset_draft(self):
        for rec in self:
            rec.state = "draft"
        return True

    # ---------- GS1 parsing ----------

    @api.model
    def parse_gs1(self, barcode):
        """Parse a GS1 element string using the Application-Identifier subset.

        Returns a dict with keys among: gtin, lot, exp_date, prod_date, serial,
        weight, count.  Unknown AIs are silently skipped.  Empty dict if the
        barcode is not GS1-shaped (no leading AI we recognise).
        """
        if not barcode:
            return {}

        # Strip leading FNC1 marker if present.
        s = barcode.lstrip("\x1d")
        result = {}
        i = 0
        n = len(s)
        recognised_any = False

        while i < n:
            # Try AIs of length 2 then 3 then 4 (we only use 2..3 here).
            ai = None
            ai_len = 0
            for cand_len in (4, 3, 2):
                if i + cand_len <= n:
                    cand = s[i : i + cand_len]
                    if cand in _GS1_AI or _GS1_WEIGHT_RE.match(cand):
                        ai = cand
                        ai_len = cand_len
                        break
            if ai is None:
                # Not recognisable — stop parsing.
                break

            recognised_any = True
            i += ai_len

            # Weight AIs: 310n (kg), 320n (lb) — fixed 6 numeric digits.
            wmatch = _GS1_WEIGHT_RE.match(ai)
            if wmatch:
                unit = "kg" if wmatch.group(1) == "310" else "lb"
                decimals = int(wmatch.group(2))
                raw = s[i : i + 6]
                i += 6
                weight = _parse_weight_kg((raw, decimals))
                if weight is not None:
                    result["weight"] = weight
                    result["weight_unit"] = unit
                continue

            key, fixed_len, parser = _GS1_AI[ai]
            if fixed_len is not None:
                raw = s[i : i + fixed_len]
                i += fixed_len
            else:
                # variable: read until FNC1 (\x1d) or end of string.
                fnc1 = s.find("\x1d", i)
                if fnc1 == -1:
                    raw = s[i:]
                    i = n
                else:
                    raw = s[i:fnc1]
                    i = fnc1 + 1
            try:
                result[key] = parser(raw)
            except Exception:  # noqa: BLE001 — defensive parser
                result[key] = raw

        return result if recognised_any else {}

    # ---------- barcode handling ----------

    def on_barcode_scanned(self, barcode):
        """Called by the barcodes.barcode_events_mixin when a barcode is scanned.

        Looks up product/lot from the raw barcode and appends a scan line.
        Also runs GS1 parsing — if the scan is a GS1 element string carrying a
        GTIN/lot, those take precedence over the raw lookup.
        """
        self.ensure_one()
        if self.state == "draft":
            self.state = "scanning"

        status = "ok"
        gs1 = self.parse_gs1(barcode)
        product = self.env["product.product"]
        lot = self.env["stock.lot"]

        # GS1 path first.
        if gs1.get("gtin"):
            product = self.env["product.product"]._resolve_barcode(gs1["gtin"])
        if gs1.get("lot") and product:
            lot = self.env["stock.lot"].search(
                [("name", "=", gs1["lot"]), ("product_id", "=", product.id)],
                limit=1,
            )

        # Plain lookup fallback.
        if not product:
            product = self.env["product.product"]._resolve_barcode(barcode)
        if not lot:
            lot = self.env["stock.lot"].search([("name", "=", barcode)], limit=1)
        if not product and lot:
            product = lot.product_id

        if not product and not lot:
            status = "not_found"

        if product and self.line_ids.filtered(
            lambda l: l.product_id.id == product.id and l.lot_id.id == (lot.id if lot else False)
        ):
            status = "duplicate"

        qty = float(gs1.get("weight") or 1.0)

        self.env["custom.barcode.scan.line"].create(
            {
                "session_id": self.id,
                "product_id": product.id if product else False,
                "lot_id": lot.id if lot else False,
                "raw_barcode": barcode,
                "quantity": qty,
                "status": status,
                "x_gs1_parsed": json.dumps(gs1) if gs1 else False,
            }
        )
        return True

    # ---------- apply to picking ----------

    def action_apply_to_picking(self):
        """Reconcile OK scan lines against the related picking's move lines.

        For every OK scan line:
          * find a stock.move.line on the picking with the same product (and
            optionally same lot) — preferring lines that still have remaining
            demand;
          * if no compatible move.line exists, create one against the first
            matching stock.move;
          * accumulate qty_done from the scan quantities;
          * if the scan carries a lot and the move.line has no lot, set it —
            creating the lot via stock.lot if it doesn't already exist.

        Posts a chatter summary on both the session and the picking.
        """
        Lot = self.env["stock.lot"]
        MoveLine = self.env["stock.move.line"]

        for rec in self:
            if not rec.picking_id:
                _logger.info(
                    "custom.barcode.scan.session %s: no picking set, skipping apply",
                    rec.name,
                )
                continue

            picking = rec.picking_id
            applied = 0
            created_move_lines = 0

            for line in rec.line_ids.filtered(lambda l: l.status == "ok" and l.product_id):
                # Resolve / create the lot if the scan carries one.
                lot_rec = line.lot_id
                if not lot_rec and line.raw_barcode and line.product_id.tracking in ("lot", "serial"):
                    gs1 = line.get_gs1_dict()
                    lot_name = gs1.get("lot") or line.raw_barcode
                    lot_rec = Lot.search(
                        [
                            ("name", "=", lot_name),
                            ("product_id", "=", line.product_id.id),
                        ],
                        limit=1,
                    )
                    if not lot_rec:
                        lot_rec = Lot.create(
                            {
                                "name": lot_name,
                                "product_id": line.product_id.id,
                                "company_id": picking.company_id.id,
                            }
                        )

                # Odoo renamed stock.move.line.qty_done -> quantity (and
                # reserved_uom_qty -> quantity_product_uom). Resolve the names
                # ONCE here; hardcoding qty_done below raised
                # "Invalid field 'qty_done' in 'stock.move.line'" on 19.
                ml_fields = MoveLine._fields
                qty_field = "qty_done" if "qty_done" in ml_fields else "quantity"
                demand_field = "reserved_uom_qty" if "reserved_uom_qty" in ml_fields else "quantity_product_uom"

                # Find a candidate move.line: same product, same picking, no lot
                # mismatch.  Prefer lines still short of their demand.
                ml_domain = [
                    ("picking_id", "=", picking.id),
                    ("product_id", "=", line.product_id.id),
                ]
                if lot_rec:
                    ml_domain.append(("lot_id", "in", (False, lot_rec.id)))

                candidates = MoveLine.search(ml_domain)
                # Preference: lines still short of expected qty.
                candidates = candidates.sorted(key=lambda m: (m[qty_field] or 0.0) - (m[demand_field] or 0.0))
                ml = candidates[:1]

                if not ml:
                    # Create a new move.line on the first move for that product.
                    move = picking.move_ids.filtered(lambda m: m.product_id.id == line.product_id.id)[:1]
                    if not move:
                        # No demand on the picking for this product — skip.
                        continue
                    ml = MoveLine.create(
                        {
                            "move_id": move.id,
                            "picking_id": picking.id,
                            "product_id": line.product_id.id,
                            "product_uom_id": move.product_uom.id,
                            "location_id": move.location_id.id,
                            "location_dest_id": move.location_dest_id.id,
                            "lot_id": lot_rec.id if lot_rec else False,
                            qty_field: 0.0,
                            "company_id": picking.company_id.id,
                        }
                    )
                    created_move_lines += 1

                ml = ml[:1]
                current = ml[qty_field] or 0.0
                ml[qty_field] = current + (line.quantity or 0.0)
                if lot_rec and not ml.lot_id:
                    ml.lot_id = lot_rec.id
                applied += 1

            ok_lines = rec.line_ids.filtered(lambda l: l.status == "ok")
            summary = _(
                "<b>Barcode Scan applied</b><br/>"
                "Session: %(name)s<br/>"
                "Total scanned: %(total)s<br/>"
                "OK: %(ok)s | Not found: %(nf)s | Duplicate: %(dup)s<br/>"
                "Move lines updated: %(applied)s (new: %(created)s)"
            ) % {
                "name": rec.name,
                "total": len(rec.line_ids),
                "ok": len(ok_lines),
                "nf": len(rec.line_ids.filtered(lambda l: l.status == "not_found")),
                "dup": len(rec.line_ids.filtered(lambda l: l.status == "duplicate")),
                "applied": applied,
                "created": created_move_lines,
            }
            _logger.info(
                "custom.barcode.scan.session %s applied %s lines to picking %s",
                rec.name,
                applied,
                picking.name,
            )
            picking.message_post(body=summary, subtype_xmlid="mail.mt_note")
            rec.message_post(body=summary, subtype_xmlid="mail.mt_note")
        return True

    # ---------- mobile / kiosk ----------

    def action_open_kiosk(self):
        """Open the session in a mobile/kiosk-friendly form view."""
        self.ensure_one()
        view = self.env.ref(
            "custom_barcode.view_custom_barcode_scan_session_kiosk_form",
            raise_if_not_found=False,
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Kiosk Scan"),
            "res_model": "custom.barcode.scan.session",
            "res_id": self.id,
            "view_mode": "form",
            "view_id": view.id if view else False,
            "target": "fullscreen",
            "context": dict(self.env.context, form_view_initial_mode="edit"),
        }

    # ---------- report data helper ----------

    def get_picking_summary_data(self):
        """Helper used by the QWeb-PDF report.  Returns per-product expected vs
        scanned with deviation %.
        """
        self.ensure_one()
        picking = self.picking_id
        expected = {}
        if picking:
            for move in picking.move_ids:
                expected.setdefault(move.product_id.id, 0.0)
                expected[move.product_id.id] += move.product_uom_qty
        scanned = {}
        for line in self.line_ids.filtered(lambda l: l.status == "ok" and l.product_id):
            scanned.setdefault(line.product_id.id, 0.0)
            scanned[line.product_id.id] += line.quantity

        product_ids = set(expected.keys()) | set(scanned.keys())
        Product = self.env["product.product"].browse(list(product_ids))
        rows = []
        for product in Product:
            exp = expected.get(product.id, 0.0)
            sc = scanned.get(product.id, 0.0)
            if exp:
                deviation = (sc - exp) / exp * 100.0
            elif sc:
                deviation = 100.0
            else:
                deviation = 0.0
            rows.append(
                {
                    "product": product,
                    "expected": exp,
                    "scanned": sc,
                    "deviation": deviation,
                }
            )
        return rows
