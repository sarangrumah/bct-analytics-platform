# -*- coding: utf-8 -*-
"""PPS/EVShop H2H API surface exposed by Odoo (Revamp II: Odoo as switcher).

Seven routes mimic the vendor PPS contract so POS POS re-points its base
URL to Odoo unchanged. Each request is MD5-verified (isolated in
``pps_signature``), resolved to a mitra credential, then mapped onto the native
``custom.ppob.transaction`` engine / wallet / provider adapter.

Auth model note: the PPS contract has no nonce and (mostly) no timestamp, so the
compensating controls are the per-mitra MD5 secret + IP allowlist; money
idempotency is the DB ``unique(mitra_id, idempotency_key)`` -- a duplicate Sell
with the same ``notrx`` returns the ORIGINAL result, never a second sell. We do
NOT nonce-guard, because StatusTrx is polled repeatedly with the same notrx.
"""

import json
import logging

from odoo import http
from odoo.exceptions import UserError
from odoo.http import request

from odoo.addons.custom_core.controllers.secure_endpoint import _check_ip_whitelist
from . import pps_signature
from ..pps_errors import resolve as resolve_error, state_to_status
from ..pps_message import sale_message, with_deposit

_logger = logging.getLogger(__name__)


def _client_ip():
    httpreq = request.httprequest
    remote = httpreq.environ.get("HTTP_X_FORWARDED_FOR") or httpreq.remote_addr or ""
    return remote.split(",")[0].strip()


class _InquiryCarrier:
    """Lightweight, non-persisted transaction stand-in for stateless inquiries
    (checkNoCustomer / inquiry-pln). Exposes the attributes provider adapters
    read; custom biller adapters used for inquiry must only read these."""

    def __init__(self, product, provider, msisdn, company, currency):
        self.product_id = product
        self.provider_id = provider
        self.msisdn = msisdn
        self.sell_price = 0.0
        self.cost_price = 0.0
        self.company_id = company
        self.currency_id = currency
        self.name = "INQUIRY"


class PpsGatewayController(http.Controller):
    # ------------------------------------------------------------------
    # Auth + parsing
    # ------------------------------------------------------------------

    def _authn(self, endpoint, params):
        user = params.get("user")
        if not user:
            return None, "BAD_FORMAT"
        cred = (
            request.env["custom.ppob.pps.mitra.credential"]
            .sudo()
            .search([("pps_user", "=", user), ("status", "=", "active")], limit=1)
        )
        if not cred:
            return None, "UNKNOWN_USER"
        if not _check_ip_whitelist(cred.ip_whitelist, _client_ip()):
            _logger.warning("PPS %s rejected: IP not allowed for %s", endpoint, user)
            return None, "IP_NOT_ALLOWED"
        if not pps_signature.verify(endpoint, params, cred._get_password()):
            _logger.warning("PPS %s rejected: bad signature for %s", endpoint, user)
            return None, "BAD_SIGNATURE"
        return cred, None

    @staticmethod
    def _form():
        return {k: v for k, v in request.httprequest.form.items()}

    @staticmethod
    def _json_body():
        try:
            return json.loads(request.httprequest.get_data() or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    @staticmethod
    def _resp(payload, http_status=200):
        return request.make_json_response(payload, status=http_status)

    # ------------------------------------------------------------------
    # Response builders
    # ------------------------------------------------------------------

    def _sell_ok(self, txn):
        return self._resp(
            {
                "Status": state_to_status(txn.state),
                "ServerIDTrx": txn.pps_serveridtrx,
                "ClientNoTrx": txn.idempotency_key,
                "Message": sale_message(txn),
            }
        )

    def _sell_err(self, code, notrx, serverid=None):
        status, message = resolve_error(code)
        return self._resp(
            {
                "Status": status,
                "ServerIDTrx": serverid,
                "ClientNoTrx": notrx,
                "Message": message,
            }
        )

    @staticmethod
    def _map_usererror(msg):
        low = (msg or "").lower()
        if "insufficient wallet" in low:
            return "INSUFFICIENT_DEPOSIT"
        if "no sku map" in low or "no active provider route" in low:
            return "NO_ROUTE"
        if "no wallet" in low:
            return "NO_ROUTE"
        if "cap exceeded" in low:
            return "ADAPTER_FAIL"
        return "ADAPTER_FAIL"

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    def _company(self):
        # auth="public", readonly=False requests have no logged-in user, so env.company can be
        # empty and the transaction's currency/company defaults won't fire.
        return request.env.company or request.env["res.company"].sudo().search([], order="id", limit=1)

    def _product_by_code(self, code):
        return request.env["custom.ppob.product"].sudo().search([("code", "=", code)], limit=1)

    def _provider_for(self, product):
        sku = (
            request.env["custom.ppob.provider.sku.map"]
            .sudo()
            .search(
                [("product_id", "=", product.id), ("active", "=", True), ("provider_id.status", "=", "active")],
                order="priority asc, id asc",
                limit=1,
            )
        )
        return sku.provider_id if sku else request.env["custom.ppob.provider"].sudo()

    def _sell_price(self, mitra, product):
        Tier = request.env["custom.ppob.price.tier"].sudo()
        price = 0.0
        if hasattr(Tier, "_get_sell_price"):
            try:
                price = Tier._get_sell_price(mitra, product) or 0.0
            except Exception:
                price = 0.0
        return price or product.denom or product.cost_price_default or 0.0

    # ------------------------------------------------------------------
    # 1. SELL
    # ------------------------------------------------------------------

    @http.route("/pps/sell", type="http", auth="public", readonly=False, methods=["POST"], csrf=False)
    def sell(self, **_):
        params = self._form()
        notrx = params.get("notrx")
        cred, err = self._authn("sell", params)
        if err:
            return self._sell_err(err, notrx)
        if not notrx or not params.get("produk") or not params.get("mdn"):
            return self._sell_err("BAD_FORMAT", notrx)

        Txn = request.env["custom.ppob.transaction"].sudo()
        existing = Txn.search([("mitra_id", "=", cred.mitra_id.id), ("idempotency_key", "=", notrx)], limit=1)
        if existing:
            return self._sell_ok(existing)  # idempotent: original result

        product = self._product_by_code(params["produk"])
        if not product:
            return self._sell_err("PRODUCT_NOT_FOUND", notrx)

        serverid = Txn._pps_next_serveridtrx()
        sell_price = self._sell_price(cred.mitra_id, product)
        company = self._company()
        try:
            with request.env.cr.savepoint():
                txn = Txn.with_company(company).create(
                    {
                        "mitra_id": cred.mitra_id.id,
                        "product_id": product.id,
                        "msisdn": params["mdn"],
                        "idempotency_key": notrx,
                        "sell_price": sell_price,
                        "company_id": company.id,
                        "currency_id": company.currency_id.id,
                        "pps_serveridtrx": serverid,
                        "pps_produk": params["produk"],
                        "pps_callback_url": cred.callback_url or False,
                    }
                )
                txn.action_dispatch()
        except UserError as exc:
            return self._sell_err(self._map_usererror(str(exc)), notrx, serverid)
        return self._sell_ok(txn)

    # ------------------------------------------------------------------
    # 2 & 3. STATUS TRANSACTION (+ WITH DEPOSIT)
    # ------------------------------------------------------------------

    def _status(self, endpoint, with_dep):
        params = self._form()
        notrx = params.get("notrx")
        cred, err = self._authn(endpoint, params)
        if err:
            return self._sell_err(err, notrx)
        txn = (
            request.env["custom.ppob.transaction"]
            .sudo()
            .search([("mitra_id", "=", cred.mitra_id.id), ("idempotency_key", "=", notrx)], limit=1)
        )
        if not txn:
            return self._sell_err("NOT_FOUND", notrx)
        message = sale_message(txn)
        if with_dep:
            message = with_deposit(message, txn.wallet_id.balance)
        return self._resp(
            {
                "Status": state_to_status(txn.state),
                "ServerIDTrx": txn.pps_serveridtrx,
                "ClientNoTrx": notrx,
                "Message": message,
            }
        )

    @http.route("/pps/statustrx", type="http", auth="public", readonly=False, methods=["POST"], csrf=False)
    def statustrx(self, **_):
        return self._status("statustrx", with_dep=False)

    @http.route("/pps/statustrxwithdeposit", type="http", auth="public", readonly=False, methods=["POST"], csrf=False)
    def statustrxwithdeposit(self, **_):
        return self._status("statustrxdeposit", with_dep=True)

    # ------------------------------------------------------------------
    # 4. CHECK CUSTOMER (e-wallet name inquiry)
    # ------------------------------------------------------------------

    @http.route("/pps/checknocustomer", type="http", auth="public", readonly=False, methods=["POST"], csrf=False)
    def checknocustomer(self, **_):
        params = self._form()
        customer_no = params.get("customer_no")
        cred, err = self._authn("checknocustomer", params)
        if err:
            status, message = resolve_error(err)
            return self._resp(
                {"status": status, "message": message, "data": {"no_tujuan": customer_no or "", "nama": ""}}
            )
        product = self._product_by_code(params.get("product"))
        if not product:
            return self._resp(
                {
                    "status": "1",
                    "message": "Produk tidak ditemukan",
                    "data": {"no_tujuan": customer_no or "", "nama": ""},
                }
            )
        result = self._run_inquiry(product, customer_no)
        if result is None or not result.ok:
            return self._resp(
                {"status": "1", "message": "Cek Pelanggan Gagal", "data": {"no_tujuan": customer_no or "", "nama": ""}}
            )
        raw = result.raw or {}
        name = raw.get("nama") or raw.get("customerName") or ""
        return self._resp(
            {
                "status": "0",
                "message": "NOMOR: %s@NAMA:%s" % (customer_no or "", name),
                "data": {"no_tujuan": customer_no or "", "nama": name},
            }
        )

    # ------------------------------------------------------------------
    # 5. INQUIRY PLN
    # ------------------------------------------------------------------

    @http.route("/pps/inquiry-pln", type="http", auth="public", readonly=False, methods=["POST"], csrf=False)
    def inquiry_pln(self, **_):
        params = self._json_body()
        if params is None:
            return self._resp({"status": "1", "message": "Format request salah", "data": {}})
        customer_no = params.get("customerNumber")
        cred, err = self._authn("inquiry_pln", params)
        if err:
            status, message = resolve_error(err)
            return self._resp({"status": status, "message": message, "data": {}})
        code = request.env["ir.config_parameter"].sudo().get_param("custom_ppob_pps_gateway.pln_product_code", "")
        product = (
            self._product_by_code(code)
            if code
            else request.env["custom.ppob.product"].sudo().search([("inquiry_required", "=", True)], limit=1)
        )
        if not product:
            return self._resp({"status": "1", "message": "PLN product not configured", "data": {}})
        result = self._run_inquiry(product, customer_no)
        if result is None or not result.ok:
            return self._resp(
                {
                    "status": "1",
                    "message": "PLN Inquiry failed",
                    "data": {"meterNumber": "", "customerName": "", "subscriberID": "", "electricityTariff": ""},
                }
            )
        raw = result.raw or {}
        return self._resp(
            {
                "status": "0",
                "message": "Successfully",
                "data": {
                    "meterNumber": raw.get("meterNumber", ""),
                    "customerName": raw.get("customerName", ""),
                    "subscriberID": raw.get("subscriberID", customer_no or ""),
                    "electricityTariff": raw.get("electricityTariff", ""),
                },
            }
        )

    def _run_inquiry(self, product, customer_no):
        provider = self._provider_for(product)
        if not provider:
            return None
        try:
            adapter = provider._get_adapter()
        except Exception:
            _logger.exception("inquiry: cannot instantiate adapter")
            return None
        carrier = _InquiryCarrier(product, provider, customer_no, request.env.company, request.env.company.currency_id)
        try:
            return adapter.inquiry(carrier)
        except Exception as exc:
            _logger.warning("inquiry adapter raised: %s", exc)
            return None

    # ------------------------------------------------------------------
    # 6. GET GAMELIST
    # ------------------------------------------------------------------

    @http.route("/pps/game-list", type="http", auth="public", readonly=False, methods=["POST"], csrf=False)
    def game_list(self, **_):
        params = self._json_body()
        if params is None:
            return self._resp({"status": "1", "message": "Format request salah", "data": []})
        cred, err = self._authn("game_list", params)
        if err:
            status, message = resolve_error(err)
            return self._resp({"status": status, "message": message, "data": []})
        GameField = request.env["custom.ppob.pps.game.field"].sudo()
        products = GameField.search([]).mapped("product_id")
        data = []
        for product in products:
            fields_ = GameField.search([("product_id", "=", product.id)])
            data.append(
                {
                    "product": product.code,
                    "product_desc": product.name,
                    "fields": [{"name": f.key, "type": f.field_type} for f in fields_],
                }
            )
        return self._resp({"status": "0", "message": "OK", "data": data})

    # ------------------------------------------------------------------
    # 7. DIRECT TOP UP (game)
    # ------------------------------------------------------------------

    @http.route("/pps/direct-topup", type="http", auth="public", readonly=False, methods=["POST"], csrf=False)
    def direct_topup(self, **_):
        params = self._json_body()
        if params is None:
            return self._sell_err("BAD_FORMAT", None)
        notrx = params.get("notrx")
        cred, err = self._authn("direct_topup", params)
        if err:
            return self._sell_err(err, notrx)
        if not notrx or not params.get("product"):
            return self._sell_err("BAD_FORMAT", notrx)

        Txn = request.env["custom.ppob.transaction"].sudo()
        existing = Txn.search([("mitra_id", "=", cred.mitra_id.id), ("idempotency_key", "=", notrx)], limit=1)
        if existing:
            return self._sell_ok(existing)

        product = self._product_by_code(params["product"])
        if not product:
            return self._sell_err("PRODUCT_NOT_FOUND", notrx)

        field = params.get("field") or {}
        missing = self._missing_game_fields(product, field)
        if missing:
            return self._sell_err("BAD_FORMAT", notrx)

        serverid = Txn._pps_next_serveridtrx()
        sell_price = self._sell_price(cred.mitra_id, product)
        company = self._company()
        try:
            with request.env.cr.savepoint():
                txn = Txn.with_company(company).create(
                    {
                        "mitra_id": cred.mitra_id.id,
                        "product_id": product.id,
                        "msisdn": str(field.get("userid") or "-"),
                        "idempotency_key": notrx,
                        "sell_price": sell_price,
                        "company_id": company.id,
                        "currency_id": company.currency_id.id,
                        "pps_serveridtrx": serverid,
                        "pps_produk": params["product"],
                        "pps_callback_url": cred.callback_url or False,
                        "dynamic_field": field,
                    }
                )
                txn.action_dispatch()
        except UserError as exc:
            return self._sell_err(self._map_usererror(str(exc)), notrx, serverid)
        return self._sell_ok(txn)

    def _missing_game_fields(self, product, field):
        required = (
            request.env["custom.ppob.pps.game.field"]
            .sudo()
            .search([("product_id", "=", product.id), ("required", "=", True)])
        )
        return [f.key for f in required if not field.get(f.key)]
