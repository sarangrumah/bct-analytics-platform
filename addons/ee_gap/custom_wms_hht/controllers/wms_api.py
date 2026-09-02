# -*- coding: utf-8 -*-
# License: LGPL-3
"""Task API for the WMS handheld shell.

Session-authenticated (`auth="user"`) rather than device-HMAC on purpose: the
shell page itself is already served behind `auth="user"`, and every stock
movement made here must be attributed to the *operator*, not to a shared
device key. The HMAC `/api/hht/*` endpoints stay untouched for thin scanners
that post raw scans without a session.

Odoo 19: `type="json"` is a deprecated alias — routes declare `jsonrpc`.
"""

from __future__ import annotations

import logging

from odoo import _, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

_logger = logging.getLogger(__name__)

# A scan can mean several things. Resolution order matters: a bin barcode and
# a package name never collide in practice, but a lot name and a product
# default_code can, so products are resolved before lots.
SCAN_KINDS = ("location", "package", "product", "lot", "picking")

# The states a handheld considers "work still to do". Kept in one place so the
# list screens, the queue badges and the scan-to-open search cannot drift.
OPEN_PICKING_STATES = ("assigned", "confirmed", "waiting")


def _ok(**payload):
    return dict(payload, ok=True)


def _err(message, code="ERROR", **extra):
    return dict(extra, ok=False, error=str(message), error_code=code)


class _BadQuantity(ValueError):
    """A quantity arrived over JSON-RPC that must never reach a stock field."""


def _qty(value):
    """Parse a quantity from a JSON-RPC payload, refusing NaN/Inf.

    ``float()`` happily accepts the strings ``"NaN"``, ``"Infinity"`` and
    ``"-inf"``. A NaN written to a stock quantity poisons every later
    comparison (``nan == nan`` is False), so the damage surfaces far from the
    scan that caused it, as unexplained stock that can be neither picked nor
    counted. Reject at the edge instead.
    """
    try:
        qty = float(value)
    except (TypeError, ValueError) as exc:
        raise _BadQuantity(_("Quantity is not a number: %s") % (value,)) from exc
    if qty != qty or qty in (float("inf"), float("-inf")):
        raise _BadQuantity(_("Quantity is not a finite number: %s") % (value,))
    return qty


def guarded(fn):
    """Turn business exceptions into a JSON error instead of a 500 + traceback.

    A handheld has no way to show an Odoo error page; an unhandled exception
    reads as a frozen screen to the operator.
    """

    def _inner(self, **kw):
        try:
            return fn(self, **kw)
        except _BadQuantity as exc:
            return _err(exc, code="BAD_QUANTITY")
        except (UserError, ValidationError) as exc:
            return _err(exc, code="BUSINESS")
        except AccessError as exc:
            return _err(exc, code="ACCESS")
        except Exception as exc:  # noqa: BLE001 - last line of defence
            _logger.exception("WMS HHT %s failed", fn.__name__)
            return _err(exc, code="INTERNAL")

    _inner.__name__ = fn.__name__
    return _inner


class WmsHhtApi(http.Controller):
    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _warehouse(warehouse_id=None):
        """Resolve the warehouse this handheld is working in.

        Picking `search(limit=1)` was wrong on any multi-warehouse database:
        demo_wms answered with the stock company's default "My Company"
        warehouse and every queue read 0 while JDC was full of work. Absent an
        explicit choice, prefer the warehouse that actually has open transfers.
        """
        env = request.env
        Warehouse = env["stock.warehouse"]
        if warehouse_id:
            wh = Warehouse.browse(int(warehouse_id)).exists()
            if wh:
                return wh
        warehouses = Warehouse.search([("company_id", "=", env.company.id)])
        if len(warehouses) <= 1:
            return warehouses[:1]
        counts = dict(
            env["stock.picking"]._read_group(
                [
                    ("state", "in", ("assigned", "confirmed", "waiting")),
                    ("picking_type_id.warehouse_id", "in", warehouses.ids),
                ],
                groupby=["picking_type_id.warehouse_id"],
                aggregates=["__count"],
            )
        )
        busiest = max(warehouses, key=lambda w: counts.get(w, 0))
        return busiest if counts.get(busiest, 0) else warehouses[:1]

    @http.route("/hht/wms/warehouses", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def warehouses(self, **_kw):
        env = request.env
        warehouses = env["stock.warehouse"].search([("company_id", "=", env.company.id)])
        default = self._warehouse()
        return _ok(
            warehouses=[{"id": w.id, "name": w.display_name, "code": w.code} for w in warehouses],
            default_id=default.id if default else False,
        )

    @staticmethod
    def _resolve_product(code):
        """Turn one scanned string into a product, however it was encoded.

        EAN-13, the GTIN-14 alias, a GS1 element string, or the internal
        reference — the receive screen, the stock check and the document
        search must all agree on what a label means, so they share this.
        """
        env = request.env
        gs1 = env["custom.barcode.scan.session"].parse_gs1(code)
        lookup = gs1.get("gtin") or code
        product = env["product.product"]._resolve_barcode(lookup)
        if not product and gs1.get("gtin"):
            # GS1 AI 01 is 14 digits; the master data may hold the EAN-13.
            product = env["product.product"]._resolve_barcode(gs1["gtin"].lstrip("0"))
        if not product:
            product = env["product.product"].search([("default_code", "=", code)], limit=1)
        return product, gs1

    @staticmethod
    def _find_pickings(base_domain, term, limit=40):
        """Find every transfer a scanned or typed code could mean.

        An operator scans what is physically in front of them: the transfer
        label, the vendor's delivery note (which lands in ``origin``), the
        carrier's tracking number, or — when the paperwork is missing or
        unreadable — an item out of the carton. Matching only
        ``stock.picking.name`` made all but the first of those answer "Unknown
        barcode", so the list just sat there unfiltered and the operator had
        to scroll: the behaviour reported from the floor.

        Returns ``(pickings, matched_by)``; ``matched_by`` tells the screen why
        it matched so it can say so instead of silently changing the list.
        """
        Picking = request.env["stock.picking"]
        open_domain = base_domain + [("state", "in", OPEN_PICKING_STATES)]

        # An exact document number is unambiguous and must win over a partial
        # that happens to also match — scanning WH/IN/00007 never means "the
        # eleven receipts whose origin contains 7".
        exact = Picking.search(open_domain + [("name", "=ilike", term)], limit=limit)
        if exact:
            return exact, "name"

        text_domain = ["|", "|", ("name", "ilike", term), ("origin", "ilike", term), ("partner_id.name", "ilike", term)]
        if "carrier_tracking_ref" in Picking._fields:
            text_domain = ["|"] + text_domain + [("carrier_tracking_ref", "ilike", term)]
        text = Picking.search(open_domain + text_domain, limit=limit)
        if text:
            return text, "reference"

        product, _gs1 = WmsHhtApi._resolve_product(term)
        if not product:
            lot = request.env["stock.lot"].search([("name", "=", term)], limit=1)
            product = lot.product_id
        if product:
            by_product = Picking.search(open_domain + [("move_ids.product_id", "=", product.id)], limit=limit)
            if by_product:
                return by_product, "product"

        # Last resort, and deliberately outside the open states: a transfer the
        # operator is holding paperwork for may already be validated or still
        # draft. Finding it and being told its state beats "Unknown barcode".
        closed = Picking.search(base_domain + ["|", ("name", "=ilike", term), ("origin", "ilike", term)], limit=limit)
        return closed, "closed" if closed else ""

    @staticmethod
    def _picking_payload(picking, with_lines=False):
        data = {
            "id": picking.id,
            "name": picking.name,
            "state": picking.state,
            "partner": picking.partner_id.display_name or "",
            "origin": picking.origin or "",
            "scheduled_date": picking.scheduled_date and str(picking.scheduled_date) or "",
            "picking_type": picking.picking_type_id.display_name,
            "code": picking.picking_type_id.code,
            "location": picking.location_id.display_name,
            "location_dest": picking.location_dest_id.display_name,
            "line_count": len(picking.move_line_ids),
            "qc_state": getattr(picking, "wms_qc_state", False) or "",
        }
        if with_lines:
            data["lines"] = [WmsHhtApi._move_line_payload(ml) for ml in picking.move_line_ids]
            data["moves"] = [
                {
                    "id": m.id,
                    "product": m.product_id.display_name,
                    "default_code": m.product_id.default_code or "",
                    "demand": m.product_uom_qty,
                    "done": sum(m.move_line_ids.mapped("quantity")),
                    "uom": m.product_uom.name,
                }
                for m in picking.move_ids
            ]
        return data

    @staticmethod
    def _move_line_payload(ml):
        return {
            "id": ml.id,
            "product_id": ml.product_id.id,
            "product": ml.product_id.display_name,
            "default_code": ml.product_id.default_code or "",
            "barcode": ml.product_id.barcode or "",
            "tracking": ml.product_id.tracking,
            "quantity": ml.quantity,
            "demand": ml.quantity_product_uom,
            "uom": ml.product_uom_id.name,
            "lot": ml.lot_id.name or "",
            "lot_id": ml.lot_id.id or False,
            "expiration_date": ml.lot_id.expiration_date and str(ml.lot_id.expiration_date) or "",
            "supplier_batch_ref": getattr(ml.lot_id, "supplier_batch_ref", False) or "",
            "location": ml.location_id.display_name,
            "location_id": ml.location_id.id,
            "location_dest": ml.location_dest_id.display_name,
            "location_dest_id": ml.location_dest_id.id,
            "location_dest_barcode": ml.location_dest_id.barcode or "",
            "package": ml.result_package_id.name if "result_package_id" in ml._fields and ml.result_package_id else "",
            "picked": bool(ml.quantity),
        }

    # ------------------------------------------------------------------
    # Work queue — what the sidebar badges count
    # ------------------------------------------------------------------

    @http.route("/hht/wms/queue", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def queue(self, warehouse_id=None, **_kw):
        env = request.env
        wh = self._warehouse(warehouse_id)
        Picking = env["stock.picking"]
        base = [("state", "in", ("assigned", "confirmed", "waiting"))]
        if wh:
            base.append(("picking_type_id.warehouse_id", "=", wh.id))

        receive = Picking.search_count(base + [("picking_type_id.code", "=", "incoming")])
        internal = Picking.search_count(base + [("picking_type_id.code", "=", "internal")])
        outgoing = Picking.search_count(base + [("picking_type_id.code", "=", "outgoing")])
        qc = Picking.search_count(
            [("wms_qc_state", "=", "pending"), ("state", "=", "done")]
            if "wms_qc_state" in Picking._fields
            else [("id", "=", 0)]
        )
        counts = env["custom.cycle.count.session"].search_count([("state", "in", ("draft", "in_progress"))])
        tos = env["custom.transfer.order"].search_count([("state", "in", ("draft", "proposed"))])
        return _ok(
            warehouse=wh.display_name if wh else "",
            queue={
                "receive": receive,
                "qc": qc,
                "putaway": internal,
                "pick": outgoing,
                "count": counts,
                "bin2bin": tos,
            },
            user=env.user.name,
        )

    # ------------------------------------------------------------------
    # Universal scan resolver — one box, any barcode
    # ------------------------------------------------------------------

    @http.route("/hht/wms/scan/resolve", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def scan_resolve(self, barcode=None, expect=None, **_kw):
        """Say what a barcode *is* before anything is done with it.

        ``expect`` narrows the search when the screen already knows what it
        wants ("scan the destination bin"), so a product barcode scanned into
        a bin prompt is reported as a mismatch instead of silently resolving.
        """
        env = request.env
        code = (barcode or "").strip()
        if not code:
            return _err(_("Empty barcode"), code="EMPTY_BARCODE")
        kinds = (expect,) if expect in SCAN_KINDS else SCAN_KINDS

        for kind in kinds:
            if kind == "location":
                loc = env["stock.location"].search([("barcode", "=", code)], limit=1)
                if loc:
                    return _ok(kind="location", record=self._location_payload(loc))
            elif kind == "package":
                pack = env["stock.package"].search([("name", "=", code)], limit=1)
                if pack:
                    return _ok(kind="package", record=self._package_payload(pack))
            elif kind == "product":
                product, gs1 = self._resolve_product(code)
                if product:
                    return _ok(
                        kind="product",
                        record={
                            "id": product.id,
                            "name": product.display_name,
                            "default_code": product.default_code or "",
                            "barcode": product.barcode or "",
                            "tracking": product.tracking,
                            "uom": product.uom_id.name,
                        },
                        gs1=gs1 or {},
                    )
            elif kind == "lot":
                lot = env["stock.lot"].search([("name", "=", code)], limit=1)
                if lot:
                    return _ok(
                        kind="lot",
                        record={
                            "id": lot.id,
                            "name": lot.name,
                            "product": lot.product_id.display_name,
                            "product_id": lot.product_id.id,
                            "expiration_date": lot.expiration_date and str(lot.expiration_date) or "",
                        },
                    )
            elif kind == "picking":
                # Same search the list screens use, so a code that opens a
                # receipt there is never "unknown" here (and vice versa).
                pickings, matched_by = self._find_pickings([], code, limit=1)
                if pickings:
                    return _ok(
                        kind="picking",
                        record=self._picking_payload(pickings[0]),
                        matched_by=matched_by,
                    )

        return _err(_("Unknown barcode: %s") % code, code="NOT_FOUND", barcode=code)

    @staticmethod
    def _location_payload(loc):
        quants = request.env["stock.quant"].search([("location_id", "=", loc.id), ("quantity", "!=", 0)])
        return {
            "id": loc.id,
            "name": loc.display_name,
            "barcode": loc.barcode or "",
            "usage": loc.usage,
            "content": [
                {
                    "product": q.product_id.display_name,
                    "default_code": q.product_id.default_code or "",
                    "lot": q.lot_id.name or "",
                    "quantity": q.quantity,
                    "reserved": q.reserved_quantity,
                }
                for q in quants
            ],
        }

    @staticmethod
    def _package_payload(pack):
        quants = request.env["stock.quant"].search([("package_id", "=", pack.id)])
        moves = request.env["stock.move.line"].search([("result_package_id", "=", pack.id)], limit=5, order="id desc")
        return {
            "id": pack.id,
            "name": pack.name,
            "package_type": pack.package_type_id.name or "",
            "location": pack.location_id.display_name or "",
            "location_id": pack.location_id.id or False,
            "content": [
                {
                    "product": q.product_id.display_name,
                    "default_code": q.product_id.default_code or "",
                    "lot": q.lot_id.name or "",
                    "quantity": q.quantity,
                    "uom": q.product_id.uom_id.name,
                }
                for q in quants
            ],
            "history": [
                {
                    "picking": ml.picking_id.name or "",
                    "date": str(ml.date or ""),
                    "product": ml.product_id.display_name,
                    "quantity": ml.quantity,
                }
                for ml in moves
            ],
        }

    # ------------------------------------------------------------------
    # Picking lists (receive / putaway / pick share one shape)
    # ------------------------------------------------------------------

    @http.route("/hht/wms/pickings", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def pickings(self, code="incoming", limit=40, warehouse_id=None, query=None, **_kw):
        """The open work list, optionally narrowed by a scan or typed query.

        ``query`` is what the operator scanned on the list screen. The screen
        opens the document straight away when exactly one transfer matches,
        and shows the narrowed list when several do.
        """
        wh = self._warehouse(warehouse_id)
        base = [("picking_type_id.code", "=", code)]
        if wh:
            base.append(("picking_type_id.warehouse_id", "=", wh.id))

        term = (query or "").strip()
        if term:
            pickings, matched_by = self._find_pickings(base, term, limit=int(limit))
            return _ok(
                pickings=[self._picking_payload(p) for p in pickings],
                query=term,
                matched_by=matched_by,
            )

        pickings = request.env["stock.picking"].search(
            base + [("state", "in", OPEN_PICKING_STATES)],
            limit=int(limit),
            order="scheduled_date, priority desc",
        )
        return _ok(pickings=[self._picking_payload(p) for p in pickings], query="", matched_by="")

    @http.route("/hht/wms/picking", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def picking(self, picking_id=None, **_kw):
        picking = request.env["stock.picking"].browse(int(picking_id)).exists()
        if not picking:
            return _err(_("Transfer not found"), code="NOT_FOUND")
        return _ok(picking=self._picking_payload(picking, with_lines=True))

    # ------------------------------------------------------------------
    # Receiving
    # ------------------------------------------------------------------

    @http.route("/hht/wms/receive/scan", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def receive_scan(self, picking_id=None, barcode=None, quantity=None, supplier_batch=None, **_kw):
        """Feed one physical scan into the receipt through the scan session.

        Everything downstream — GS1 expiry, supplier batch, serial/IMEI, the
        "scan SETs the quantity" rule — already lives in
        ``custom_wms_receiving_ext``; this endpoint is deliberately a thin
        driver over it rather than a second implementation.
        """
        env = request.env
        picking = env["stock.picking"].browse(int(picking_id)).exists()
        if not picking:
            return _err(_("Transfer not found"), code="NOT_FOUND")
        code = (barcode or "").strip()
        if not code:
            return _err(_("Empty barcode"), code="EMPTY_BARCODE")

        Session = env["custom.barcode.scan.session"]
        session = Session.search(
            [("picking_id", "=", picking.id), ("state", "in", ("draft", "scanning"))],
            limit=1,
        ) or Session.create({"picking_id": picking.id})
        before = set(session.line_ids.ids)
        session.on_barcode_scanned(code)
        line = session.line_ids.filtered(lambda ln: ln.id not in before)[:1]
        if not line:
            return _err(_("Scan produced no line"), code="NO_LINE")
        if line.status == "not_found":
            line.unlink()
            return _err(_("Unknown barcode: %s") % code, code="NOT_FOUND", barcode=code)
        if quantity is not None and line.product_id.tracking != "serial":
            line.quantity = _qty(quantity)
        if supplier_batch:
            line.supplier_batch_ref = supplier_batch
        session.action_apply_to_picking()
        return _ok(
            session=session.name,
            scanned={
                "product": line.product_id.display_name,
                "quantity": line.quantity,
                "lot": line.lot_id.name or "",
                "status": line.status,
            },
            picking=self._picking_payload(picking, with_lines=True),
        )

    @http.route("/hht/wms/receive/validate", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def receive_validate(self, picking_id=None, **_kw):
        picking = request.env["stock.picking"].browse(int(picking_id)).exists()
        if not picking:
            return _err(_("Transfer not found"), code="NOT_FOUND")
        picking.button_validate()
        return _ok(
            picking=self._picking_payload(picking),
            qc_required=picking.wms_qc_state == "pending" if "wms_qc_state" in picking._fields else False,
        )

    @http.route("/hht/wms/qc", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def qc(self, picking_id=None, verdict="pass", notes=None, **_kw):
        picking = request.env["stock.picking"].browse(int(picking_id)).exists()
        if not picking:
            return _err(_("Transfer not found"), code="NOT_FOUND")
        if notes:
            picking.wms_qc_notes = notes
        if verdict == "pass":
            picking.action_wms_qc_pass()
        else:
            picking.action_wms_qc_fail()
        release = picking.wms_qc_release_picking_id
        return _ok(
            qc_state=picking.wms_qc_state,
            release_picking=self._picking_payload(release) if release else None,
        )

    # ------------------------------------------------------------------
    # Stock check
    # ------------------------------------------------------------------

    @http.route("/hht/wms/stock/lookup", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def stock_lookup(self, barcode=None, warehouse_id=None, **_kw):
        """Answer "what is this and where is it?" for a single scanned product.

        Read-only on purpose: an operator standing in an aisle needs to check
        an item without the risk of moving it. Everything shown here already
        exists elsewhere in the app — the product resolution is the same one
        the receive screen uses (EAN-13, GTIN-14 alias, GS1 element string,
        internal reference), and the bin ranking is the put-away engine's —
        so a stock check and a real put-away can never disagree.
        """
        env = request.env
        code = (barcode or "").strip()
        if not code:
            return _err(_("Empty barcode"), code="EMPTY_BARCODE")

        product, gs1 = self._resolve_product(code)
        if not product:
            # A lot/serial label identifies its product just as well, and is
            # what an operator finds on a box in the aisle.
            lot = env["stock.lot"].search([("name", "=", code)], limit=1)
            if lot:
                product = lot.product_id
        if not product:
            return _err(_("Unknown barcode: %s") % code, code="NOT_FOUND", barcode=code)

        wh = self._warehouse(warehouse_id)
        quant_domain = [("product_id", "=", product.id), ("location_id.usage", "=", "internal")]
        if wh:
            quant_domain.append(("location_id", "child_of", wh.view_location_id.id))
        quants = env["stock.quant"].search(quant_domain)

        # One row per bin: an operator walks to a bin, not to a quant, so
        # several lots in the same bin must read as one stop with its lots
        # spelled out underneath.
        bins = {}
        for q in quants:
            row = bins.setdefault(
                q.location_id.id,
                {
                    "location_id": q.location_id.id,
                    "location": q.location_id.complete_name,
                    "barcode": q.location_id.barcode or "",
                    "quantity": 0.0,
                    "reserved": 0.0,
                    "lots": [],
                },
            )
            row["quantity"] += q.quantity
            row["reserved"] += q.reserved_quantity
            if q.lot_id:
                row["lots"].append(
                    {
                        "name": q.lot_id.name,
                        "quantity": q.quantity,
                        "expiration_date": (q.lot_id.expiration_date and str(q.lot_id.expiration_date)[:10] or ""),
                    }
                )
        rows = sorted(bins.values(), key=lambda r: (-r["quantity"], r["location"]))
        for row in rows:
            row["available"] = row["quantity"] - row["reserved"]
            row["lots"].sort(key=lambda l: -l["quantity"])

        on_hand = sum(r["quantity"] for r in rows)
        reserved = sum(r["reserved"] for r in rows)

        # Bins holding stock, by `parent_path`, so a zone-level suggestion can
        # be told apart from a genuinely empty one: rules often target a zone
        # (JDC-HD) while the stock sits in a child bin (JDC-HD-A-01), and
        # comparing ids alone reports every such zone as empty.
        stocked_paths = [q.location_id.parent_path or "" for q in quants]

        suggestions = []
        # Put-away configuration is readable only by the Put-away groups, and
        # a stock check is meant for any warehouse operator: losing the bin
        # suggestions must not cost the operator the stock figures they came
        # for. Degrade to "no suggestions" instead of failing the whole screen.
        suggestions_denied = False
        try:
            self._collect_suggestions(env, product, wh, stocked_paths, suggestions)
        except AccessError:
            suggestions_denied = True

        return _ok(
            product={
                "id": product.id,
                "name": product.display_name,
                "default_code": product.default_code or "",
                "barcode": product.barcode or "",
                "category": product.categ_id.display_name or "",
                "uom": product.uom_id.name,
                "tracking": product.tracking,
                "weight": product.weight or 0.0,
                "volume": product.volume or 0.0,
            },
            scanned=code,
            gs1=gs1 or {},
            warehouse=wh.name if wh else "",
            totals={
                "on_hand": on_hand,
                "reserved": reserved,
                "available": on_hand - reserved,
                "bin_count": len(rows),
            },
            bins=rows,
            suggestions=suggestions,
            suggestions_denied=suggestions_denied,
        )

    @staticmethod
    def _collect_suggestions(env, product, wh, stocked_paths, suggestions):
        if wh:
            seen = set()
            for p in env["custom.putaway.engine"].propose_for_product(product, wh):
                # Two rules can rank the same bin; the operator walks there
                # once, so only the best-scoring proposal for it is shown.
                if p["location_id"] in seen:
                    continue
                seen.add(p["location_id"])
                loc = env["stock.location"].browse(p["location_id"])
                prefix = loc.parent_path or ""
                suggestions.append(
                    {
                        "location_id": loc.id,
                        "location": loc.complete_name,
                        "barcode": loc.barcode or "",
                        "score": p["score"],
                        "reason": p["reason"],
                        "tier": p.get("tier"),
                        # Flagging a bin the product already sits in turns a
                        # bare ranking into an actionable one: consolidating
                        # into an occupied bin beats opening a new one.
                        "has_stock": bool(prefix and any(path.startswith(prefix) for path in stocked_paths)),
                    }
                )
                if len(suggestions) == 3:
                    break

    # ------------------------------------------------------------------
    # Putaway
    # ------------------------------------------------------------------

    @http.route("/hht/wms/putaway/suggest", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def putaway_suggest(self, picking_id=None, **_kw):
        env = request.env
        picking = env["stock.picking"].browse(int(picking_id)).exists()
        if not picking:
            return _err(_("Transfer not found"), code="NOT_FOUND")
        engine = env["custom.putaway.engine"]
        rows = []
        for ml in picking.move_line_ids:
            proposals = engine.propose(ml)[:3]
            rows.append(
                {
                    "move_line": self._move_line_payload(ml),
                    "proposals": [
                        {
                            "location_id": p["location_id"],
                            "location": env["stock.location"].browse(p["location_id"]).display_name,
                            "barcode": env["stock.location"].browse(p["location_id"]).barcode or "",
                            "score": p["score"],
                            "reason": p["reason"],
                            "tier": p.get("tier"),
                        }
                        for p in proposals
                    ],
                }
            )
        return _ok(picking=self._picking_payload(picking), rows=rows)

    @http.route("/hht/wms/putaway/apply", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def putaway_apply(self, move_line_id=None, location_id=None, barcode=None, **_kw):
        env = request.env
        ml = env["stock.move.line"].browse(int(move_line_id)).exists()
        if not ml:
            return _err(_("Line not found"), code="NOT_FOUND")
        location = None
        if location_id:
            location = env["stock.location"].browse(int(location_id)).exists()
        elif barcode:
            location = env["stock.location"].search([("barcode", "=", barcode.strip())], limit=1)
        if not location:
            return _err(_("Unknown bin: %s") % (barcode or location_id), code="NOT_FOUND")
        # The category-reservation constraint fires here when the bin is
        # flagged wms_enforce_categ — surfaced to the operator as a business
        # error rather than a 500.
        ml.location_dest_id = location.id
        return _ok(move_line=self._move_line_payload(ml))

    # ------------------------------------------------------------------
    # Picking / packing
    # ------------------------------------------------------------------

    @http.route("/hht/wms/pick/confirm", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def pick_confirm(self, move_line_id=None, quantity=None, barcode=None, **_kw):
        """Confirm a pick line — the scan must match the line's product/lot."""
        env = request.env
        ml = env["stock.move.line"].browse(int(move_line_id)).exists()
        if not ml:
            return _err(_("Line not found"), code="NOT_FOUND")
        if barcode:
            code = barcode.strip()
            product = env["product.product"]._resolve_barcode(code)
            lot = env["stock.lot"].search([("name", "=", code)], limit=1)
            if product and product != ml.product_id:
                return _err(
                    _("Wrong item: scanned %(scanned)s, expected %(expected)s")
                    % {"scanned": product.display_name, "expected": ml.product_id.display_name},
                    code="MISMATCH",
                )
            if lot and ml.lot_id and lot != ml.lot_id:
                return _err(
                    _("Wrong lot: scanned %(scanned)s, expected %(expected)s")
                    % {"scanned": lot.name, "expected": ml.lot_id.name},
                    code="MISMATCH",
                )
            if not product and not lot:
                return _err(_("Unknown barcode: %s") % code, code="NOT_FOUND")
        ml.quantity = _qty(quantity) if quantity is not None else ml.quantity_product_uom
        if "picked" in ml.move_id._fields:
            ml.move_id.picked = True
        return _ok(move_line=self._move_line_payload(ml))

    @http.route("/hht/wms/pick/pack", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def pick_pack(self, picking_id=None, move_line_ids=None, **_kw):
        """Put the confirmed lines in a package (Odoo 19: ``stock.package``)."""
        env = request.env
        picking = env["stock.picking"].browse(int(picking_id)).exists()
        if not picking:
            return _err(_("Transfer not found"), code="NOT_FOUND")
        lines = (
            env["stock.move.line"].browse([int(i) for i in move_line_ids])
            if move_line_ids
            else picking.move_line_ids.filtered(lambda ml: ml.quantity)
        )
        if not lines:
            return _err(_("Nothing picked yet"), code="EMPTY")
        result = lines.with_context(picking_id=picking.id).action_put_in_pack()
        pack = lines.mapped("result_package_id")[:1]
        if not pack and isinstance(result, dict) and result.get("res_id"):
            pack = env["stock.package"].browse(result["res_id"])
        return _ok(
            package=self._package_payload(pack) if pack else None,
            picking=self._picking_payload(picking, with_lines=True),
        )

    @http.route("/hht/wms/pick/validate", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def pick_validate(self, picking_id=None, **_kw):
        picking = request.env["stock.picking"].browse(int(picking_id)).exists()
        if not picking:
            return _err(_("Transfer not found"), code="NOT_FOUND")
        res = picking.button_validate()
        # A wizard (backorder / immediate transfer) cannot be shown on a
        # handheld — report it instead of leaving the operator staring at a
        # transfer that never validated.
        if isinstance(res, dict) and res.get("res_model"):
            return _err(
                _("This transfer needs the %s wizard on the desktop.") % res["res_model"],
                code="WIZARD_REQUIRED",
                wizard=res["res_model"],
            )
        return _ok(picking=self._picking_payload(picking))

    # ------------------------------------------------------------------
    # Package
    # ------------------------------------------------------------------

    @http.route("/hht/wms/package", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def package(self, barcode=None, package_id=None, **_kw):
        env = request.env
        pack = None
        if package_id:
            pack = env["stock.package"].browse(int(package_id)).exists()
        elif barcode:
            pack = env["stock.package"].search([("name", "=", barcode.strip())], limit=1)
        if not pack:
            return _err(_("Package not found: %s") % (barcode or package_id), code="NOT_FOUND")
        return _ok(package=self._package_payload(pack))

    @http.route("/hht/wms/package/move", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def package_move(self, package_id=None, barcode=None, warehouse_id=None, **_kw):
        """Move a whole package to a scanned bin (bin-to-bin, no paperwork)."""
        env = request.env
        pack = env["stock.package"].browse(int(package_id)).exists()
        if not pack:
            return _err(_("Package not found"), code="NOT_FOUND")
        dest = env["stock.location"].search([("barcode", "=", (barcode or "").strip())], limit=1)
        if not dest:
            return _err(_("Unknown bin: %s") % barcode, code="NOT_FOUND")
        quants = env["stock.quant"].search([("package_id", "=", pack.id), ("quantity", ">", 0)])
        if not quants:
            return _err(_("Package is empty"), code="EMPTY")
        picking_type = env["stock.picking.type"].search(
            [("code", "=", "internal"), ("warehouse_id", "=", self._warehouse(warehouse_id).id)], limit=1
        )
        picking = env["stock.picking"].create(
            {
                "picking_type_id": picking_type.id,
                "location_id": quants[0].location_id.id,
                "location_dest_id": dest.id,
                "origin": _("HHT package move %s") % pack.name,
            }
        )
        for quant in quants:
            move = env["stock.move"].create(
                {
                    "product_id": quant.product_id.id,
                    "product_uom_qty": quant.quantity,
                    "product_uom": quant.product_id.uom_id.id,
                    "location_id": quant.location_id.id,
                    "location_dest_id": dest.id,
                    "picking_id": picking.id,
                }
            )
            move._action_confirm()
        picking.action_assign()
        for ml in picking.move_line_ids:
            ml.quantity = ml.quantity_product_uom
            ml.result_package_id = pack.id
        picking.button_validate()
        return _ok(picking=self._picking_payload(picking), package=self._package_payload(pack))

    # ------------------------------------------------------------------
    # Cycle count
    # ------------------------------------------------------------------

    @http.route("/hht/wms/count/sessions", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def count_sessions(self, query=None, **_kw):
        domain = [("state", "in", ("draft", "in_progress", "reviewing"))]
        term = (query or "").strip()
        if term:
            # A count sheet is found by its own number, by the plan it came
            # from, or by scanning any bin or item that is on it — the three
            # things an operator can actually read off the floor.
            product, _gs1 = self._resolve_product(term)
            sub = ["|", ("name", "ilike", term), ("plan_id.name", "ilike", term)]
            sub = ["|"] + sub + [("line_ids.location_id.barcode", "=", term)]
            if product:
                sub = ["|"] + sub + [("line_ids.product_id", "=", product.id)]
            domain += sub
        sessions = request.env["custom.cycle.count.session"].search(domain, limit=20)
        return _ok(
            query=term,
            sessions=[
                {
                    "id": s.id,
                    "name": s.name,
                    "state": s.state,
                    "plan": s.plan_id.name or "",
                    "method": s.plan_id.method or "",
                    "line_count": s.line_count,
                    "counted": len(s.line_ids.filtered(lambda ln: ln.status != "pending")),
                    "variance_count": s.variance_count,
                }
                for s in sessions
            ],
        )

    @http.route("/hht/wms/count/lines", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def count_lines(self, session_id=None, **_kw):
        session = request.env["custom.cycle.count.session"].browse(int(session_id)).exists()
        if not session:
            return _err(_("Session not found"), code="NOT_FOUND")
        if session.state == "draft":
            session.action_start()
        return _ok(
            session={"id": session.id, "name": session.name, "state": session.state},
            lines=[
                {
                    "id": ln.id,
                    "location": ln.location_id.display_name,
                    "location_barcode": ln.location_id.barcode or "",
                    "product": ln.product_id.display_name,
                    "default_code": ln.product_id.default_code or "",
                    "barcode": ln.product_id.barcode or "",
                    "lot": ln.lot_id.name or "",
                    "expected_qty": ln.expected_qty,
                    "counted_qty": ln.counted_qty,
                    "variance_qty": ln.variance_qty,
                    "status": ln.status,
                }
                for ln in session.line_ids
            ],
        )

    @http.route("/hht/wms/count/submit", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def count_submit(self, line_id=None, quantity=0.0, remark=None, **_kw):
        line = request.env["custom.cycle.count.line"].browse(int(line_id)).exists()
        if not line:
            return _err(_("Line not found"), code="NOT_FOUND")
        line.action_count(_qty(quantity))
        if remark:
            line.remark = remark
        return _ok(
            line={
                "id": line.id,
                "counted_qty": line.counted_qty,
                "variance_qty": line.variance_qty,
                "status": line.status,
            }
        )

    # ------------------------------------------------------------------
    # Bin-to-bin transfer orders
    # ------------------------------------------------------------------

    @http.route("/hht/wms/bin2bin/list", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def bin2bin_list(self, query=None, **_kw):
        domain = [("state", "in", ("draft", "proposed"))]
        term = (query or "").strip()
        if term:
            # Scanning the bin you are standing at is the natural way in here:
            # it answers "is there anything to move out of / into this bin?".
            product, _gs1 = self._resolve_product(term)
            sub = [
                "|",
                "|",
                ("name", "ilike", term),
                ("source_location_id.barcode", "=", term),
                ("target_location_id.barcode", "=", term),
            ]
            if product:
                sub = ["|"] + sub + [("product_id", "=", product.id)]
            domain += sub
        tos = request.env["custom.transfer.order"].search(domain, limit=40)
        return _ok(
            query=term,
            orders=[
                {
                    "id": to.id,
                    "name": to.name,
                    "state": to.state,
                    "product": to.product_id.display_name,
                    "default_code": to.product_id.default_code or "",
                    "lot": to.lot_id.name or "",
                    "planned_qty": to.planned_qty,
                    "source": to.source_location_id.display_name,
                    "source_barcode": to.source_location_id.barcode or "",
                    "target": to.target_location_id.display_name,
                    "target_barcode": to.target_location_id.barcode or "",
                    # A transfer order carries a stock.move, not a picking —
                    # the engine materialises bare moves.
                    "picking": to.stock_move_id.picking_id.name if to.stock_move_id.picking_id else "",
                    "reason": to.rule_id.name or "",
                }
                for to in tos
            ],
        )

    @http.route("/hht/wms/bin2bin/execute", type="jsonrpc", auth="user", methods=["POST"])
    @guarded
    def bin2bin_execute(self, transfer_order_id=None, source_barcode=None, target_barcode=None, **_kw):
        env = request.env
        to = env["custom.transfer.order"].browse(int(transfer_order_id)).exists()
        if not to:
            return _err(_("Transfer order not found"), code="NOT_FOUND")
        for scanned, expected, label in (
            (source_barcode, to.source_location_id, _("source")),
            (target_barcode, to.target_location_id, _("target")),
        ):
            if scanned and expected.barcode and scanned.strip() != expected.barcode:
                return _err(
                    _("Wrong %(label)s bin: scanned %(scanned)s, expected %(expected)s")
                    % {"label": label, "scanned": scanned, "expected": expected.barcode},
                    code="MISMATCH",
                )
        move = to.stock_move_id
        if not move:
            return _err(_("This order has no stock move to execute"), code="NO_MOVE")
        if move.state == "draft":
            move._action_confirm()
        move._action_assign()
        for ml in move.move_line_ids:
            ml.quantity = ml.quantity_product_uom
        move.picked = True
        move._action_done()
        if to.state != "done":
            to.state = "done"
        return _ok(
            order={"id": to.id, "name": to.name, "state": to.state},
            moved=move.quantity,
        )
