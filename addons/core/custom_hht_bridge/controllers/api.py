# -*- coding: utf-8 -*-
# License: LGPL-3
"""HHT REST API — all routes HMAC-signed via @secure_endpoint('hht')."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any

from odoo import _, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from odoo.addons.custom_core.controllers.secure_endpoint import secure_endpoint

_logger = logging.getLogger(__name__)

_MAX_PRODUCTS = 5000


def _json_body() -> dict:
    httpreq = request.httprequest
    body = httpreq.get_data() or b""
    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8")) or {}
    except (ValueError, UnicodeDecodeError):
        return {}


def _device_from_request():
    """Resolve hht.device from X-Device-Key header (api_key)."""
    key = request.httprequest.headers.get("X-Device-Key", "")
    return request.env["hht.device"].sudo()._find_by_api_key(key)


def _log_scan(
    device,
    *,
    action: str,
    barcode: str | None = None,
    location_id: int | None = None,
    qty: float | None = None,
    lot_id: int | None = None,
    picking_id: int | None = None,
    result: str = "ok",
    error_message: str | None = None,
    payload: Any = None,
):
    httpreq = request.httprequest
    remote = (httpreq.environ.get("HTTP_X_FORWARDED_FOR") or httpreq.remote_addr or "").split(",")[0].strip()
    vals = {
        "device_id": device.id if device else False,
        "action": action,
        "barcode": (barcode or "")[:128] or False,
        "location_id": location_id or False,
        "qty": qty or 0.0,
        "lot_id": lot_id or False,
        "picking_id": picking_id or False,
        "result": result,
        "error_message": (error_message or "")[:1024] or False,
        "payload": payload or False,
        "client_ip": remote or False,
    }
    if not vals["device_id"]:
        # Without a device we cannot write (required). Log silently.
        _logger.warning("hht scan dropped — no device: action=%s err=%s", action, error_message)
        return request.env["hht.scan.log"].sudo().browse()
    return request.env["hht.scan.log"].sudo().create(vals)


def _json(payload: dict, status: int = 200):
    return request.make_json_response(payload, status=status)


def _handle_scan(device, data: dict) -> dict:
    """Single dispatcher for /api/hht/scan and /api/hht/datawedge.

    Returns {ok, result|error, next_action_hint?}.
    """
    if not device:
        _log_scan(
            device,
            action=data.get("action") or "lookup",
            barcode=data.get("barcode"),
            result="error",
            error_message="UNKNOWN_DEVICE",
            payload=data,
        )
        return {"ok": False, "error": "UNKNOWN_DEVICE"}

    action = (data.get("action") or "lookup").strip()
    barcode = (data.get("barcode") or "").strip()
    if not barcode:
        _log_scan(device, action=action, result="error", error_message="EMPTY_BARCODE", payload=data)
        return {"ok": False, "error": "EMPTY_BARCODE"}

    location_id = data.get("location_id") or None
    qty = data.get("qty") or 0.0
    lot = data.get("lot") or None
    picking_id = data.get("picking_id") or None

    env = request.env(su=True)
    lot_rec = False
    if lot:
        lot_rec = env["stock.lot"].search([("name", "=", lot)], limit=1)

    try:
        if action == "lookup":
            product = env["product.product"]._resolve_barcode(barcode)
            if not product:
                product = env["product.product"].search([("default_code", "=", barcode)], limit=1)
            result_payload = {
                "product_id": product.id if product else None,
                "product_name": product.display_name if product else None,
                "uom": product.uom_id.name if product else None,
            }
            hint = "ready_for_qty" if product else "unknown_barcode"
        elif action in ("receipt", "issue", "transfer", "count"):
            # Resolve product, validate location.
            product = env["product.product"]._resolve_barcode(barcode)
            if not product:
                product = env["product.product"].search([("default_code", "=", barcode)], limit=1)
            if not product:
                raise UserError(_("Unknown barcode: %s") % barcode)
            if not location_id:
                raise UserError(_("Missing location_id for action=%s") % action)
            result_payload = {
                "product_id": product.id,
                "qty": float(qty or 0.0),
                "location_id": int(location_id),
                "applied": False,  # actual stock move recorded asynchronously
                "lot_id": lot_rec.id if lot_rec else None,
            }
            hint = "scan_next"
        elif action == "handover":
            # Looking up a BAST document by reference.
            bast = env["custom.bast.document"].search(
                [("name", "=", barcode)],
                limit=1,
            )
            if not bast:
                raise UserError(_("BAST not found: %s") % barcode)
            result_payload = {
                "bast_id": bast.id,
                "state": getattr(bast, "state", None),
            }
            hint = "ready_to_sign"
        else:
            raise UserError(_("Unsupported action: %s") % action)
    except (UserError, ValidationError, AccessError) as e:
        _log_scan(
            device,
            action=action,
            barcode=barcode,
            location_id=int(location_id) if location_id else None,
            qty=float(qty or 0.0),
            lot_id=lot_rec.id if lot_rec else None,
            picking_id=int(picking_id) if picking_id else None,
            result="error",
            error_message=str(e),
            payload=data,
        )
        return {"ok": False, "error": str(e)}

    log = _log_scan(
        device,
        action=action,
        barcode=barcode,
        location_id=int(location_id) if location_id else None,
        qty=float(qty or 0.0),
        lot_id=lot_rec.id if lot_rec else None,
        picking_id=int(picking_id) if picking_id else None,
        result="ok",
        payload=data,
    )
    device._touch_seen(summary=f"{action}:{barcode[:64]}")
    return {
        "ok": True,
        "result": result_payload,
        "scan_log_id": log.id if log else None,
        "next_action_hint": hint,
    }


class HhtApi(http.Controller):
    # ------------------------------------------------------------------
    # /api/hht/scan
    # ------------------------------------------------------------------
    @http.route("/api/hht/scan", type="http", auth="public", methods=["POST"], csrf=False)
    @secure_endpoint("hht")
    def scan(self, **_kw):
        data = _json_body()
        device = _device_from_request()
        return _json(_handle_scan(device, data))

    # ------------------------------------------------------------------
    # /api/hht/sync — idempotent batch
    # ------------------------------------------------------------------
    @http.route("/api/hht/sync", type="http", auth="public", methods=["POST"], csrf=False)
    @secure_endpoint("hht")
    def sync(self, **_kw):
        data = _json_body()
        device = _device_from_request()
        if not device:
            return _json({"ok": False, "error": "UNKNOWN_DEVICE"}, status=200)
        batch_id = (data.get("batch_id") or "")[:64]
        items = data.get("items") or []
        Queue = request.env["hht.sync.queue"].sudo()

        max_items = int(
            request.env["ir.config_parameter"].sudo().get_param("custom_hht_bridge.sync_batch_max_items", "100")
        )
        if len(items) > max_items:
            return _json({"ok": False, "error": "BATCH_TOO_LARGE", "max_items": max_items}, status=200)

        results = []
        seen_client_ids = set()
        for item in items:
            client_id = (item.get("client_id") or "").strip()
            if not client_id:
                results.append({"client_id": None, "ok": False, "error": "MISSING_CLIENT_ID"})
                continue
            # In-batch dedupe.
            if client_id in seen_client_ids:
                results.append({"client_id": client_id, "ok": True, "result": "deduplicated"})
                continue
            seen_client_ids.add(client_id)
            existing = Queue.search(
                [
                    ("device_id", "=", device.id),
                    ("client_id", "=", client_id),
                ],
                limit=1,
            )
            if existing:
                results.append({"client_id": client_id, "ok": True, "result": "deduplicated", "state": existing.state})
                continue
            try:
                queue_rec = Queue.create(
                    {
                        "device_id": device.id,
                        "client_id": client_id,
                        "batch_id": batch_id or False,
                        "action": item.get("action") or False,
                        "payload": item,
                        "state": "queued",
                    }
                )
                # Optimistic apply: run scan handler synchronously.
                outcome = _handle_scan(device, item)
                queue_rec.write(
                    {
                        "state": "applied" if outcome.get("ok") else "failed",
                        "error": outcome.get("error") if not outcome.get("ok") else False,
                    }
                )
                results.append(
                    {
                        "client_id": client_id,
                        "ok": outcome.get("ok"),
                        "result": outcome.get("result"),
                        "error": outcome.get("error"),
                    }
                )
            except Exception as e:
                _logger.exception("sync item failed: %s", e)
                results.append({"client_id": client_id, "ok": False, "error": str(e)})
        device._touch_seen(summary=f"sync:{batch_id or '-'}:{len(items)}")
        return _json({"ok": True, "results": results})

    # ------------------------------------------------------------------
    # /api/hht/manifest — preload + ETag
    # ------------------------------------------------------------------
    @http.route("/api/hht/manifest", type="http", auth="public", methods=["GET"], csrf=False)
    @secure_endpoint("hht")
    def manifest(self, **_kw):
        env = request.env(su=True)
        locations = env["stock.location"].search_read(
            [("usage", "in", ("internal", "transit"))],
            ["id", "name", "complete_name", "write_date"],
            limit=2000,
        )
        products = env["product.product"].search_read(
            [("sale_ok", "=", True), ("active", "=", True)],
            ["id", "default_code", "barcode", "display_name", "uom_id", "write_date"],
            limit=_MAX_PRODUCTS,
        )
        lots = env["stock.lot"].search_read(
            [],
            ["id", "name", "product_id", "write_date"],
            limit=2000,
            order="write_date desc",
        )
        picking_pending_count = env["stock.picking"].search_count(
            [("state", "in", ("assigned", "confirmed", "waiting"))]
        )
        timestamps = []
        for rs in (locations, products, lots):
            for r in rs:
                wd = r.get("write_date")
                if wd:
                    timestamps.append(str(wd))
        etag_basis = ("|".join(sorted(timestamps)) + f"|pp={picking_pending_count}").encode("utf-8")
        etag = '"' + hashlib.sha256(etag_basis).hexdigest()[:32] + '"'
        if request.httprequest.headers.get("If-None-Match") == etag:
            return request.make_response("", status=304, headers=[("ETag", etag)])
        payload = {
            "ok": True,
            "result": {
                "locations": locations,
                "products": products,
                "lots": lots,
                "picking_pending_count": picking_pending_count,
            },
        }
        return request.make_response(
            json.dumps(payload, default=str),
            headers=[
                ("Content-Type", "application/json"),
                ("ETag", etag),
                ("Cache-Control", "private, max-age=60"),
            ],
        )

    # ------------------------------------------------------------------
    # /api/hht/bast/sign
    # ------------------------------------------------------------------
    @http.route("/api/hht/bast/sign", type="http", auth="public", methods=["POST"], csrf=False)
    @secure_endpoint("hht")
    def bast_sign(self, **_kw):
        data = _json_body()
        device = _device_from_request()
        if not device:
            return _json({"ok": False, "error": "UNKNOWN_DEVICE"}, status=200)
        bast_id = data.get("bast_id")
        party = (data.get("party") or "").strip()
        signature_b64 = data.get("signature_b64") or ""
        if not bast_id or party not in ("from", "to") or not signature_b64:
            _log_scan(device, action="handover", result="error", error_message="BAD_BAST_PAYLOAD", payload=data)
            return _json({"ok": False, "error": "BAD_BAST_PAYLOAD"})
        env = request.env(su=True)
        bast = env["custom.bast.document"].browse(int(bast_id)).exists()
        if not bast:
            _log_scan(device, action="handover", result="error", error_message="BAST_NOT_FOUND", payload=data)
            return _json({"ok": False, "error": "BAST_NOT_FOUND"})
        try:
            sig_raw = base64.b64decode(signature_b64, validate=True)
        except Exception:
            return _json({"ok": False, "error": "BAD_SIGNATURE_B64"})
        attachment = env["ir.attachment"].create(
            {
                "name": f"bast-sign-{party}-{bast.id}.png",
                "datas": base64.b64encode(sig_raw).decode("ascii"),
                "res_model": "custom.bast.document",
                "res_id": bast.id,
                "mimetype": "image/png",
            }
        )
        # Soft-write: only set fields if they exist on the model.
        write_vals = {}
        if "signature_from" in bast._fields and party == "from":
            write_vals["signature_from"] = base64.b64encode(sig_raw).decode("ascii")
        if "signature_to" in bast._fields and party == "to":
            write_vals["signature_to"] = base64.b64encode(sig_raw).decode("ascii")
        if data.get("gps_lat") is not None and "gps_lat" in bast._fields:
            write_vals["gps_lat"] = data.get("gps_lat")
        if data.get("gps_long") is not None and "gps_long" in bast._fields:
            write_vals["gps_long"] = data.get("gps_long")
        if write_vals:
            bast.write(write_vals)
        _log_scan(device, action="handover", barcode=bast.name, result="ok", payload=data)
        device._touch_seen(summary=f"bast:{bast.name}:{party}")
        return _json(
            {
                "ok": True,
                "result": {
                    "bast_id": bast.id,
                    "attachment_id": attachment.id,
                    "party": party,
                },
            }
        )

    # ------------------------------------------------------------------
    # /api/hht/me
    # ------------------------------------------------------------------
    @http.route("/api/hht/me", type="http", auth="public", methods=["GET"], csrf=False)
    @secure_endpoint("hht")
    def me(self, **_kw):
        device = _device_from_request()
        if not device:
            return _json({"ok": False, "error": "UNKNOWN_DEVICE"}, status=200)
        user = device.user_id
        permissions = []
        if user.has_group("custom_hht_bridge.group_hht_operator"):
            permissions.append("hht_operator")
        if user.has_group("custom_hht_bridge.group_hht_admin"):
            permissions.append("hht_admin")
        tenant = device.tenant_id
        return _json(
            {
                "ok": True,
                "result": {
                    "user": {"id": user.id, "name": user.name, "login": user.login},
                    "device": {
                        "id": device.id,
                        "name": device.name,
                        "device_id": device.device_id,
                        "model": device.model,
                        "enabled": device.enabled,
                    },
                    "tenant": {
                        "id": tenant.id if tenant else None,
                        "name": tenant.display_name if tenant else None,
                    },
                    "permissions": permissions,
                },
            }
        )
