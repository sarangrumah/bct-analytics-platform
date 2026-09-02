# -*- coding: utf-8 -*-
# License: LGPL-3
"""Inbound MDM product-master REST API — every route behind @secure_endpoint('mdm').

Envelope contract, identical on every route and every outcome::

    {"status": "ok",    "data": {...},  "error": null}
    {"status": "error", "data": null,   "error": {"code": "...", "message": "..."}}

Tracebacks never cross the wire; unexpected exceptions are logged with their stack
and answered with the stable code ``INTERNAL_ERROR``.

Routing type note (same reasoning as ``custom_wms_integration``): these routes use
``type="json2"``, the Odoo 19 flat-JSON dispatcher. ``type="json"`` is a deprecated
alias for ``type="jsonrpc"`` which (a) forces POST, so the GET routes below could not
exist, (b) re-wraps the return value in a ``{"jsonrpc", "result"}`` envelope, and
(c) json-dumps the ``Response`` object ``@secure_endpoint`` returns when it rejects a
request. ``json2`` returns dicts verbatim and passes ``Response`` objects through.

The two POST routes also declare ``readonly=False``. Odoo 19 hands a route a
read-only transaction unless it says otherwise, and a multi-worker deployment
enforces that -- staging a request would fail with "cannot execute INSERT in a
read-only transaction". A single-worker server happens not to, which is exactly the
kind of difference that only shows up in production.

One consequence of ``json2``: it parses the request body itself, so a syntactically
invalid JSON body is rejected by the dispatcher before any handler runs. The caller
still gets HTTP 400, but with werkzeug's error shape rather than the envelope above.
Everything past parsing -- wrong shape, missing keys, oversize batch -- is ours.

Authentication is a static API key plus a mandatory IP allow-list, configured per
database through ``ir.config_parameter``::

    custom_core.secure_endpoint.mdm.auth_mode      = api_key
    custom_core.secure_endpoint.mdm.api_keys       = <key>[,<next key during rotation>]
    custom_core.secure_endpoint.mdm.allowed_cidrs  = <Mulesoft egress CIDRs>   # required
    custom_core.secure_endpoint.mdm.max_body_bytes = 5242880

This is deliberately weaker than the platform's HMAC standard, at the sender's
request; the allow-list is the compensating control and the module can be moved to
HMAC by flipping ``auth_mode`` once Mulesoft can sign.
"""

from __future__ import annotations

import hashlib
import json
import logging

from odoo import fields, http
from odoo.http import request

from odoo.addons.custom_core.controllers.secure_endpoint import secure_endpoint

_logger = logging.getLogger(__name__)

SCOPE = "mdm"
VERSION = "19.0.1.0.0"

DEFAULT_MAX_ITEMS = 1000
LOOKUP_MAX_LIMIT = 1000


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


def _respond(payload, status=200):
    """Non-200 answers must carry a real HTTP status, not just a code in the body."""
    if status == 200:
        return payload
    return request.make_json_response(payload, status=status)


def _raw_body() -> bytes:
    return request.httprequest.get_data() or b""


def _query() -> dict:
    """json2 merges body and path args only; GET parameters come off werkzeug."""
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


def _param(name, default=""):
    return request.env["ir.config_parameter"].sudo().get_param(f"retail_import.{name}", default)


def _enabled():
    return _param("mdm_api_enabled", "0") in ("1", "true", "True")


def _environment():
    """Which system the caller has actually reached.

    UAT and production are reachable on the same host and the same certificate,
    separated only by a path prefix — which the caller cannot see once their client
    is configured. So the endpoint states it outright, and the database name makes it
    unambiguous rather than a label someone has to trust. A caller can assert on this
    before a run and refuse to write to the wrong system.

    Set ``retail_import.mdm_environment`` per database; unset reads as ``unknown``,
    which is deliberately not a reassuring value.
    """
    return {
        "environment": _param("mdm_environment", "unknown"),
        "database": request.env.cr.dbname,
    }


def _guard(handler, route):
    try:
        return handler()
    except ValueError as exc:
        return _respond(_error("INVALID_PAYLOAD", str(exc)), 400)
    except Exception:  # noqa: BLE001
        _logger.exception("MDM API %s failed", route)
        return _respond(_error("INTERNAL_ERROR", "The request could not be processed."), 500)


class MdmProductApi(http.Controller):
    # ==================================================================
    # POST /api/mdm/products — ingest
    # ==================================================================
    @http.route(
        "/api/mdm/products",
        type="json2",
        auth="none",
        methods=["POST"],
        csrf=False,
        save_session=False,
        readonly=False,
    )
    @secure_endpoint(SCOPE)
    def products(self, **_kw):
        return _guard(self._ingest, "/api/mdm/products")

    def _ingest(self):
        if not _enabled():
            return _respond(_error("SERVICE_DISABLED", "The MDM product API is not enabled on this database."), 503)

        raw = _raw_body()
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else None
        except (ValueError, UnicodeDecodeError) as exc:
            return _respond(_error("INVALID_PAYLOAD", f"Body is not valid JSON: {exc}"), 400)

        # A single object and an array of them are both accepted: the near-realtime
        # feed sends one item per message, an initial load sends batches.
        if isinstance(parsed, dict):
            items = [parsed]
        elif isinstance(parsed, list):
            items = parsed
        else:
            return _respond(_error("INVALID_PAYLOAD", "Expected a JSON object or an array of objects."), 400)

        if not items:
            return _respond(_error("EMPTY_PAYLOAD", "No items in the request."), 400)
        if any(not isinstance(i, dict) for i in items):
            return _respond(_error("INVALID_PAYLOAD", "Every array element must be an object."), 400)

        max_items = _int_arg({"n": _param("mdm_max_items", str(DEFAULT_MAX_ITEMS))}, "n", DEFAULT_MAX_ITEMS)
        if len(items) > max_items:
            return _respond(_error("TOO_MANY_ITEMS", f"{len(items)} items exceeds the limit of {max_items}."), 400)

        # Reject the whole batch rather than staging it half-keyed: a message we
        # cannot key is a message we could never dedupe or replay.
        missing = [
            index
            for index, item in enumerate(items)
            if not str(item.get("skuCode") or "").strip() and not str(item.get("udf2") or "").strip()
        ]
        if missing:
            return _respond(
                _error(
                    "MISSING_SKU_CODE",
                    "Items at position(s) %s have neither skuCode nor udf2." % ", ".join(map(str, missing[:20])),
                ),
                400,
            )
        seen, dupes = set(), []
        for item in items:
            key = str(item.get("skuCode") or item.get("udf2") or "").strip()
            if key in seen:
                dupes.append(key)
            seen.add(key)
        if dupes:
            return _respond(
                _error("DUPLICATE_SKU_IN_BATCH", "skuCode(s) repeated in one message: %s" % ", ".join(dupes[:20])),
                400,
            )

        headers = request.httprequest.headers
        supplied = (headers.get("X-Request-Id") or "").strip()
        dedupe_key = supplied or hashlib.sha256(raw).hexdigest()
        if (headers.get("X-Force-Reprocess") or "").strip() in ("1", "true", "True"):
            # Ops re-drive only: salting the key defeats the dedupe constraint on
            # purpose, so the same body can be processed a second time.
            dedupe_key = f"{dedupe_key}:force:{fields.Datetime.now()}"

        remote = (
            (request.httprequest.environ.get("HTTP_X_FORWARDED_FOR") or request.httprequest.remote_addr or "")
            .split(",")[0]
            .strip()
        )

        record, duplicate = (
            request.env["retail.mdm.request"].sudo().ingest(items, dedupe_key, source_ip=remote, raw=parsed)
        )
        data = {
            "requestId": record.request_id,
            "accepted": 0 if duplicate else len(items),
            "duplicate": duplicate,
            # Echoed on the write path, not only on /ping: this is the response a
            # caller sees at the moment master data is about to change.
            **_environment(),
        }
        if not duplicate:
            data["skuCodes"] = [str(i.get("skuCode") or i.get("udf2") or "") for i in items[:100]]
        # 200 (not 409) on a duplicate: a retry after a timed-out 202 is correct client
        # behaviour and must not look like an error. 202 (not 200) on acceptance: the
        # work is queued, not done.
        return _respond(_ok(data), 200 if duplicate else 202)

    # ==================================================================
    # GET /api/mdm/products/lookup — is this SKU registered?
    # ==================================================================
    @http.route("/api/mdm/products/lookup", type="json2", auth="none", methods=["GET"], csrf=False, save_session=False)
    @secure_endpoint(SCOPE)
    def lookup(self, **_kw):
        return _guard(self._lookup, "/api/mdm/products/lookup")

    def _lookup(self):
        args = _query()
        sku_code = (args.get("skuCode") or "").strip()
        sku = (args.get("sku") or "").strip()
        ean = (args.get("ean") or "").strip()
        code = (args.get("code") or "").strip()
        if not any((sku_code, sku, ean, code)):
            return _respond(_error("MISSING_QUERY", "Pass one of skuCode, sku, ean or code."), 400)

        env = request.env
        Product = env["product.product"].sudo()
        product = Product.browse()

        # Same resolution order as the X24DN importer's resolve_product, so "the API
        # says it exists" and "the importer can find it" can never disagree.
        if ean:
            product = Product._resolve_barcode(ean)
        if not product and sku_code:
            product = Product.search([("mdm_sku_code", "=", sku_code)], limit=1)
        if not product and sku:
            product = Product.search([("default_code", "=", sku)], limit=1)
        if not product and code:
            product = Product.search([("default_code", "=", code)], limit=1)
        if not product and code:
            template = env["product.template"].sudo().search([("default_code", "=", code)], limit=1)
            product = template.product_variant_id if len(template.product_variant_ids) == 1 else Product.browse()

        if product:
            return _ok(
                {
                    "found": True,
                    "skuCode": product.mdm_sku_code or False,
                    "sku": product.default_code or False,
                    "productId": product.id,
                    "templateId": product.product_tmpl_id.id,
                    "code": product.product_tmpl_id.default_code or False,
                    "name": product.display_name,
                    "active": product.active,
                    "mdmPending": product.product_tmpl_id.mdm_pending,
                    "source": product.product_tmpl_id.mdm_source or False,
                    "barcodes": self._barcodes(product),
                    "syncedAt": self._iso(product.product_tmpl_id.mdm_synced_at),
                }
            )

        # Not registered — say whether we have at least seen it in a sales file, so
        # the caller can tell "unknown SKU" from "known, waiting for its master".
        Pending = env["retail.mdm.pending.sku"].sudo()
        codes = [c for c in (sku, sku_code, code) if c]
        branches = []
        if codes:
            branches.append(("composite_code", "in", codes))
            branches.append(("item_code", "in", codes))
        if ean:
            branches.append(("ean", "=", ean))
        pending = Pending.browse()
        if branches:
            pending = Pending.search(["|"] * (len(branches) - 1) + branches, limit=1)

        data = {"found": False}
        if pending:
            data["pending"] = {
                "state": pending.state,
                "firstSeen": self._iso(pending.first_seen_at),
                "lastSeen": self._iso(pending.last_seen_at),
                "occurrences": pending.occurrence_count,
                "parkedRows": pending.parked_line_count,
            }
        return _ok(data)

    @staticmethod
    def _barcodes(product):
        codes = [product.barcode] if product.barcode else []
        codes.extend(b for b in product.barcode_ids.mapped("barcode") if b and b not in codes)
        return codes

    @staticmethod
    def _iso(value):
        return value.isoformat() + "Z" if value else False

    # ==================================================================
    # GET /api/mdm/requests/<id> — processing status
    # ==================================================================
    @http.route(
        "/api/mdm/requests/<string:request_id>",
        type="json2",
        auth="none",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    @secure_endpoint(SCOPE)
    def request_status(self, request_id, **_kw):
        return _guard(lambda: self._status(request_id), "/api/mdm/requests")

    def _status(self, request_id):
        record = request.env["retail.mdm.request"].sudo().search([("request_id", "=", request_id)], limit=1)
        if not record:
            return _respond(_error("UNKNOWN_REQUEST", f"No MDM request {request_id}."), 404)
        return _ok(
            {
                "requestId": record.request_id,
                "state": record.state,
                "dryRun": record.dry_run,
                "itemCount": record.item_count,
                "ok": record.ok_count,
                "duplicate": record.dup_count,
                "error": record.error_count,
                "needsReview": record.review_count,
                "attemptCount": record.attempt_count,
                "lastError": record.last_error or False,
                "receivedAt": self._iso(record.received_at),
                "processedAt": self._iso(record.processed_at),
                "items": [
                    {
                        "skuCode": item.sku_code or False,
                        "sku": item.prod_sku or False,
                        "state": item.state,
                        "error": item.error or False,
                        "productId": item.product_id.id or False,
                    }
                    for item in record.item_ids[:200]
                ],
            }
        )

    # ==================================================================
    # POST /api/mdm/requests/<id>/replay — ops re-drive
    # ==================================================================
    @http.route(
        "/api/mdm/requests/<string:request_id>/replay",
        type="json2",
        auth="none",
        methods=["POST"],
        csrf=False,
        save_session=False,
        readonly=False,
    )
    @secure_endpoint(SCOPE)
    def request_replay(self, request_id, **_kw):
        return _guard(lambda: self._replay(request_id), "/api/mdm/requests/replay")

    def _replay(self, request_id):
        record = request.env["retail.mdm.request"].sudo().search([("request_id", "=", request_id)], limit=1)
        if not record:
            return _respond(_error("UNKNOWN_REQUEST", f"No MDM request {request_id}."), 404)
        if record.state in ("done", "cancelled"):
            return _respond(_error("NOT_REPLAYABLE", f"Request {request_id} is {record.state}."), 409)
        record.action_replay()
        return _ok({"requestId": record.request_id, "state": record.state, "attemptCount": record.attempt_count})

    # ==================================================================
    # GET /api/mdm/pending — SKUs seen in sales but absent from the master
    # ==================================================================
    @http.route("/api/mdm/pending", type="json2", auth="none", methods=["GET"], csrf=False, save_session=False)
    @secure_endpoint(SCOPE)
    def pending(self, **_kw):
        return _guard(self._pending, "/api/mdm/pending")

    def _pending(self):
        args = _query()
        state = (args.get("state") or "pending").strip()
        limit = _int_arg(args, "limit", 200, minimum=1, maximum=LOOKUP_MAX_LIMIT)
        offset = _int_arg(args, "offset", 0, minimum=0)
        Pending = request.env["retail.mdm.pending.sku"].sudo()
        domain = [] if state == "all" else [("state", "=", state)]
        total = Pending.search_count(domain)
        rows = Pending.search(domain, limit=limit, offset=offset)
        return _ok(
            {
                "count": len(rows),
                "total": total,
                "offset": offset,
                "items": [
                    {
                        "itemCode": row.item_code or False,
                        "compositeCode": row.composite_code or False,
                        "ean": row.ean or False,
                        "description": row.description or False,
                        "storeCode": row.store_code or False,
                        "firstSeen": self._iso(row.first_seen_at),
                        "lastSeen": self._iso(row.last_seen_at),
                        "occurrences": row.occurrence_count,
                        "parkedRows": row.parked_line_count,
                        "parkedTransactions": row.parked_txn_count,
                        "state": row.state,
                    }
                    for row in rows
                ],
            }
        )

    # ==================================================================
    # GET /api/mdm/ping — connectivity + credential check, no side effects
    # ==================================================================
    @http.route("/api/mdm/ping", type="json2", auth="none", methods=["GET"], csrf=False, save_session=False)
    @secure_endpoint(SCOPE)
    def ping(self, **_kw):
        return _ok(
            {
                "pong": True,
                "enabled": _enabled(),
                "dryRun": _param("mdm_dry_run", "0") in ("1", "true", "True"),
                "version": VERSION,
                **_environment(),
            }
        )
