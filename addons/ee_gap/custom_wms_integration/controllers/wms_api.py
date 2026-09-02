# -*- coding: utf-8 -*-
# License: LGPL-3
"""Inbound WMS/host REST API — every route HMAC-signed via @secure_endpoint('wms').

Envelope contract, identical on every route and every outcome:

    {"status": "ok",    "data": {...},  "error": null}
    {"status": "error", "data": null,   "error": {"code": "...", "message": "..."}}

Tracebacks never cross the wire. Unexpected exceptions are logged server-side
with the full stack and answered with the stable code ``INTERNAL_ERROR``.

Routing type note: these routes use ``type="json2"``, the Odoo 19 flat-JSON
dispatcher. ``type="json"`` is a deprecated alias for ``type="jsonrpc"`` which
(a) forces POST — so ``GET /api/wms/stock`` could not exist, (b) re-wraps the
return value in a ``{"jsonrpc", "result"}`` envelope, breaking the contract
above, and (c) json-dumps the ``Response`` object that ``@secure_endpoint``
returns when it rejects a request. ``json2`` returns dicts verbatim and passes
``Response`` objects through untouched.
"""

from __future__ import annotations

import json
import logging

from odoo import http
from odoo.http import request

from odoo.addons.custom_core.controllers.secure_endpoint import secure_endpoint

_logger = logging.getLogger(__name__)

#: Scope name for @secure_endpoint. Configured through ir.config_parameter:
#:   custom_core.secure_endpoint.wms.secret        (required, HMAC-SHA256 key)
#:   custom_core.secure_endpoint.wms.allowed_cidrs (optional, comma separated)
SCOPE = "wms"

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000


# ----------------------------------------------------------------------
# Envelope helpers
# ----------------------------------------------------------------------


def _ok(data=None):
    return {"status": "ok", "data": data, "error": None}


def _error(code, message="", **extra):
    err = {"code": code, "message": message or code}
    if extra:
        err.update(extra)
    return {"status": "error", "data": None, "error": err}


def _body() -> dict:
    """Parse the raw request body. @secure_endpoint already HMAC-verified it."""
    raw = request.httprequest.get_data() or b""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _query() -> dict:
    """Query-string args. The json2 dispatcher only merges body + path args,
    so GET parameters have to be read off the werkzeug request directly."""
    return dict(request.httprequest.args or {})


def _int_arg(source, key, default, minimum=None, maximum=None):
    try:
        value = int(source.get(key, default))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _guard(handler, route):
    """Run a handler, converting anything unexpected into a stable error code."""
    try:
        return handler()
    except ValueError as exc:
        # Deliberate, caller-facing validation failures.
        return _error("INVALID_PAYLOAD", str(exc))
    except Exception:  # noqa: BLE001
        _logger.exception("WMS API %s failed", route)
        return _error("INTERNAL_ERROR", "The request could not be processed.")


class WmsApi(http.Controller):
    # ------------------------------------------------------------------
    # POST /api/wms/asn — Advance Ship Notice (host -> Odoo, incoming)
    # ------------------------------------------------------------------
    @http.route("/api/wms/asn", type="json2", auth="none", methods=["POST"], csrf=False, save_session=False)
    @secure_endpoint(SCOPE)
    def asn(self, **_kw):
        return _guard(lambda: self._upsert_picking(_body(), "incoming"), "/api/wms/asn")

    # ------------------------------------------------------------------
    # POST /api/wms/do — Delivery Order (host -> Odoo, outgoing)
    # ------------------------------------------------------------------
    @http.route("/api/wms/do", type="json2", auth="none", methods=["POST"], csrf=False, save_session=False)
    @secure_endpoint(SCOPE)
    def delivery_order(self, **_kw):
        return _guard(lambda: self._upsert_picking(_body(), "outgoing"), "/api/wms/do")

    def _upsert_picking(self, payload, direction):
        if not payload:
            return _error("INVALID_PAYLOAD", "Empty or unparseable JSON body.")
        if not (payload.get("external_ref") or "").strip():
            return _error("MISSING_EXTERNAL_REF", "external_ref is required and is the idempotency key.")
        Picking = request.env["stock.picking"].sudo()
        picking, created, warnings = Picking._wms_upsert_from_host(payload, direction)
        return _ok(
            {
                "picking_id": picking.id,
                "picking_name": picking.name,
                "external_ref": picking.wms_external_ref,
                "state": picking.state,
                "created": created,
                "line_count": len(picking.move_ids),
                "warnings": warnings,
            }
        )

    # ------------------------------------------------------------------
    # GET /api/wms/stock — on-hand by sku / location
    # ------------------------------------------------------------------
    @http.route("/api/wms/stock", type="json2", auth="none", methods=["GET"], csrf=False, save_session=False)
    @secure_endpoint(SCOPE)
    def stock(self, **_kw):
        # A GET carries no body, so the host signs HMAC(secret, timestamp) only.
        return _guard(self._stock, "/api/wms/stock")

    def _stock(self):
        args = _query()
        limit = _int_arg(args, "limit", DEFAULT_LIMIT, minimum=1, maximum=MAX_LIMIT)
        offset = _int_arg(args, "offset", 0, minimum=0)

        env = request.env(su=True)
        Mapping = env["wms.integration.mapping"]
        company = env.company
        domain = [("location_id.usage", "=", "internal")]

        sku = (args.get("sku") or "").strip()
        if sku:
            product = Mapping._resolve(sku, "product.product", company)
            if not product:
                return _error("UNKNOWN_SKU", "No product matches %s." % sku)
            domain.append(("product_id", "=", product.id))

        location_code = (args.get("location_code") or "").strip()
        if location_code:
            location = Mapping._resolve(location_code, "stock.location", company)
            if not location:
                return _error("UNKNOWN_LOCATION", "No location matches %s." % location_code)
            domain.append(("location_id", "child_of", location.id))

        warehouse_code = (args.get("warehouse_code") or "").strip()
        if warehouse_code:
            warehouse = env["stock.warehouse"].search(
                ["|", ("code", "=", warehouse_code), ("name", "=", warehouse_code)], limit=1
            )
            if not warehouse:
                return _error("UNKNOWN_WAREHOUSE", "No warehouse matches %s." % warehouse_code)
            domain.append(("location_id", "child_of", warehouse.view_location_id.id))

        Quant = env["stock.quant"]
        total = Quant.search_count(domain)
        quants = Quant.search(domain, limit=limit, offset=offset, order="id asc")
        rows = [
            {
                "sku": Mapping._external_code_for(q.product_id),
                "product_id": q.product_id.id,
                "location_code": Mapping._external_code_for(q.location_id),
                "location_id": q.location_id.id,
                "lot": q.lot_id.name if q.lot_id else None,
                "quantity": q.quantity,
                "reserved_quantity": q.reserved_quantity,
                "available_quantity": q.quantity - q.reserved_quantity,
                "uom": q.product_uom_id.name if q.product_uom_id else None,
            }
            for q in quants
        ]
        return _ok({"count": len(rows), "total": total, "limit": limit, "offset": offset, "lines": rows})

    # ------------------------------------------------------------------
    # POST /api/wms/ack — host acknowledges an event we pushed
    # ------------------------------------------------------------------
    @http.route("/api/wms/ack", type="json2", auth="none", methods=["POST"], csrf=False, save_session=False)
    @secure_endpoint(SCOPE)
    def ack(self, **_kw):
        return _guard(self._ack, "/api/wms/ack")

    def _ack(self):
        payload = _body()
        refs = payload.get("external_refs") or ([payload["external_ref"]] if payload.get("external_ref") else [])
        if not refs:
            return _error("MISSING_EXTERNAL_REF", "external_ref (or external_refs) is required.")
        Event = request.env["wms.integration.event"].sudo()
        host_ref = payload.get("host_ref")
        acked, unknown = [], []
        for ref in refs:
            event = Event._ack(ref, host_ref=host_ref)
            (acked if event else unknown).append(ref)
        return _ok({"acked": acked, "unknown": unknown})
