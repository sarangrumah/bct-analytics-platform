# -*- coding: utf-8 -*-
"""End-to-end HTTP tests for the PPS gateway.

Drives the real controllers over HTTP with genuine per-endpoint MD5 signatures,
so the drop-in PPS wire contract is exercised exactly as POS POS would.
Fulfilment runs against the ``ppob_mock`` adapter; a small inquiry adapter is
registered to return a customer name for checkNoCustomer.
"""

import hashlib
import json
from urllib.parse import urlencode

from odoo.tests import HttpCase, tagged

from odoo.addons.custom_ppob_provider.models.ppob_provider_adapter_base import (
    AdapterResult,
    PPOBProviderAdapter,
    register_adapter,
)


@register_adapter("ppob_test_inqname")
class _InquiryNameAdapter(PPOBProviderAdapter):
    def inquiry(self, transaction):
        return AdapterResult(
            ok=True,
            provider_ref="INQ-1",
            raw={
                "nama": "BUDI SANTOSO",
                "customerName": "BUDI SANTOSO",
                "meterNumber": "4600123",
                "subscriberID": "0400000123",
                "electricityTariff": "R1/900VA",
            },
        )

    def pay(self, transaction):
        return AdapterResult(ok=True, provider_ref="PAY-1", serial_token="SN-1", amount=transaction.cost_price)

    def status(self, provider_ref):
        return AdapterResult(ok=True, provider_ref=provider_ref, raw={"state": "success"})

    def topup(self, amount):
        return AdapterResult(ok=True, amount=amount)


def _md5(s):
    # Mirrors the vendor contract's signature formula so the tests can forge a
    # valid request; same rationale as pps_signature._md5.
    return hashlib.md5(  # nosemgrep
        (s or "").encode("utf-8"), usedforsecurity=False
    ).hexdigest()


@tagged("post_install", "-at_install", "custom_ppob_pps_gateway")
class TestPpsGateway(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.klass = cls.env["custom.ppob.product.class"].search([("code", "=", "TELKO")], limit=1)
        cls.bank = cls.env["custom.ppob.account.mapping"]._get_account("cash_bca_escrow", cls.company)

        cls.password = "secret-123"
        cls.env["ir.config_parameter"].sudo().set_param("pps.pwd.ERAUSER", cls.password)

        cls.mitra = cls.env["res.partner"].create(
            {
                "name": "Mitra PPS",
                "x_custom_ppob_is_mitra": True,
                "x_custom_ppob_mitra_code": "PPSMTR1",
            }
        )
        cls.cred = cls.env["custom.ppob.pps.mitra.credential"].create(
            {
                "mitra_id": cls.mitra.id,
                "pps_user": "ERAUSER",
                "credential_ref": "pps.pwd.ERAUSER",
                "status": "active",
            }
        )
        cls.vendor = cls.env["res.partner"].create({"name": "Biller PPS", "x_custom_ppob_is_provider": True})
        cls.product = cls.env["custom.ppob.product"].create(
            {
                "code": "PPS_TSEL5",
                "name": "TSEL 5k",
                "class_id": cls.klass.id,
                "denom": 5000.0,
                "cost_price_default": 4900.0,
            }
        )

        # Funded mitra wallet (the PPS "deposit").
        cls.wallet = cls.env["custom.ppob.wallet"].create({"partner_id": cls.mitra.id, "class_id": cls.klass.id})
        cls.wallet._atomic_credit(amount=1_000_000.0, reason="seed", counterpart_account=cls.bank, move_type="topup")

        cls.provider = cls._make_provider(cls, "PPSPROV", "ppob_test_inqname")
        cls._map(cls, cls.provider, cls.product)

        # A game product + dynamic field, for game-list / direct-topup.
        cls.game = cls.env["custom.ppob.product"].create(
            {
                "code": "PPS_GAME1",
                "name": "Mobile Legends 100D",
                "class_id": cls.klass.id,
                "denom": 25000.0,
                "cost_price_default": 24000.0,
            }
        )
        cls.env["custom.ppob.pps.game.field"].create(
            {"product_id": cls.game.id, "key": "userid", "label": "User ID", "required": True}
        )
        cls._map(cls, cls.provider, cls.game)

    def _make_provider(self, code, adapter):
        provider = self.env["custom.ppob.provider"].create(
            {
                "code": code,
                "name": code,
                "partner_id": self.vendor.id,
                "settlement_mode": "prepaid_deposit",
                "bucket_mode": "bulky",
                "adapter_class": adapter,
            }
        )
        provider.action_ensure_buckets()
        provider.bucket_ids._atomic_credit(
            dpp_amount=1_000_000.0,
            tax_amount=0.0,
            gross_amount=1_000_000.0,
            reason="seed bucket",
            counterpart_account=self.bank,
            move_type="topup",
        )
        return provider

    def _map(self, provider, product):
        self.env["custom.ppob.provider.sku.map"].create(
            {
                "provider_id": provider.id,
                "product_id": product.id,
                "provider_sku": f"SKU-{product.code}",
                "buy_price": product.cost_price_default,
                "priority": 100,
            }
        )

    # ---- HTTP helpers ----

    def _post_form(self, path, params):
        return self.url_open(
            path,
            data=urlencode(params).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded", "X-Odoo-Database": self.env.cr.dbname},
            timeout=30,
        )

    def _post_json(self, path, params):
        return self.url_open(
            path,
            data=json.dumps(params),
            headers={"Content-Type": "application/json", "X-Odoo-Database": self.env.cr.dbname},
            timeout=30,
        )

    # ------------------------------------------------------------------
    # SELL
    # ------------------------------------------------------------------

    def test_sell_success(self):
        notrx = "SELL-OK-1"
        p = {"user": "ERAUSER", "produk": self.product.code, "mdn": "081200000001", "notrx": notrx}
        p["signature"] = _md5(p["mdn"] + p["produk"] + notrx + _md5(self.password))
        resp = self._post_form("/pps/sell", p)
        self.assertEqual(resp.status_code, 200, "status=%s body=%r" % (resp.status_code, resp.text[:500]))
        r = resp.json()
        self.assertEqual(r["Status"], "0", r)
        self.assertTrue(r["ServerIDTrx"])
        self.assertEqual(r["ClientNoTrx"], notrx)
        self.assertIn("berhasil", r["Message"].lower())

    def test_sell_bad_signature_rejected(self):
        p = {"user": "ERAUSER", "produk": self.product.code, "mdn": "0812", "notrx": "BADSIG", "signature": "deadbeef"}
        r = self._post_form("/pps/sell", p).json()
        self.assertEqual(r["Status"], "1")
        self.assertIn("signature", r["Message"].lower())

    def test_sell_idempotent_no_double_debit(self):
        notrx = "SELL-IDEM-1"
        p = {"user": "ERAUSER", "produk": self.product.code, "mdn": "081200000002", "notrx": notrx}
        p["signature"] = _md5(p["mdn"] + p["produk"] + notrx + _md5(self.password))
        r1 = self._post_form("/pps/sell", p).json()
        bal = self.wallet.balance
        r2 = self._post_form("/pps/sell", p).json()
        self.assertEqual(r1["ServerIDTrx"], r2["ServerIDTrx"])
        self.wallet.invalidate_recordset()
        self.assertAlmostEqual(self.wallet.balance, bal, places=2)

    def test_sell_insufficient_deposit(self):
        # A second mitra whose wallet has zero balance and no credit line.
        mitra2 = self.env["res.partner"].create(
            {"name": "Poor Mitra", "x_custom_ppob_is_mitra": True, "x_custom_ppob_mitra_code": "PPSMTR2"}
        )
        self.env["custom.ppob.wallet"].create({"partner_id": mitra2.id, "class_id": self.klass.id})
        self.env["ir.config_parameter"].sudo().set_param("pps.pwd.POOR", "pw2")
        self.env["custom.ppob.pps.mitra.credential"].create(
            {"mitra_id": mitra2.id, "pps_user": "POOR", "credential_ref": "pps.pwd.POOR", "status": "active"}
        )
        notrx = "SELL-POOR-1"
        p = {"user": "POOR", "produk": self.product.code, "mdn": "0812", "notrx": notrx}
        p["signature"] = _md5(p["mdn"] + p["produk"] + notrx + _md5("pw2"))
        r = self._post_form("/pps/sell", p).json()
        self.assertEqual(r["Status"], "1")
        self.assertIn("deposit", r["Message"].lower())

    # ------------------------------------------------------------------
    # STATUS
    # ------------------------------------------------------------------

    def test_statustrx_and_with_deposit(self):
        notrx = "SELL-ST-1"
        p = {"user": "ERAUSER", "produk": self.product.code, "mdn": "0812", "notrx": notrx}
        p["signature"] = _md5(p["mdn"] + p["produk"] + notrx + _md5(self.password))
        self._post_form("/pps/sell", p)

        s = {"user": "ERAUSER", "notrx": notrx}
        s["signature"] = _md5(notrx + _md5(self.password))
        r = self._post_form("/pps/statustrx", s).json()
        self.assertEqual(r["Status"], "0")

        r2 = self._post_form("/pps/statustrxwithdeposit", s).json()
        self.assertEqual(r2["Status"], "0")
        self.assertIn("Deposit Anda saat ini", r2["Message"])

    # ------------------------------------------------------------------
    # CHECK CUSTOMER / INQUIRY PLN
    # ------------------------------------------------------------------

    def test_checknocustomer_returns_name(self):
        notrx = "INQ-1"
        p = {"user": "ERAUSER", "product": self.product.code, "customer_no": "085700000001", "notrx": notrx}
        p["signature"] = _md5(notrx + p["user"] + p["product"] + _md5(self.password) + p["customer_no"])
        r = self._post_form("/pps/checknocustomer", p).json()
        self.assertEqual(r["status"], "0", r)
        self.assertEqual(r["data"]["nama"], "BUDI SANTOSO")

    def test_inquiry_pln(self):
        self.env["ir.config_parameter"].sudo().set_param("custom_ppob_pps_gateway.pln_product_code", self.product.code)
        p = {"user": "ERAUSER", "customerNumber": "0400000123"}
        p["signature"] = _md5(p["customerNumber"] + p["user"] + _md5(self.password))
        r = self._post_json("/pps/inquiry-pln", p).json()
        self.assertEqual(r["status"], "0", r)
        self.assertEqual(r["data"]["customerName"], "BUDI SANTOSO")

    # ------------------------------------------------------------------
    # GAME LIST / DIRECT TOPUP
    # ------------------------------------------------------------------

    def test_game_list(self):
        p = {"user": "ERAUSER", "timestamp": "2026-07-16 10:00:00"}
        p["signature"] = _md5(p["timestamp"] + _md5(self.password))
        r = self._post_json("/pps/game-list", p).json()
        self.assertEqual(r["status"], "0")
        codes = [d["product"] for d in r["data"]]
        self.assertIn(self.game.code, codes)

    def test_direct_topup(self):
        notrx = "DTU-1"
        p = {"user": "ERAUSER", "product": self.game.code, "notrx": notrx, "field": {"userid": "9630001"}}
        p["signature"] = _md5(_md5(self.password) + p["user"] + p["product"] + notrx)
        r = self._post_json("/pps/direct-topup", p).json()
        self.assertEqual(r["Status"], "0", r)
        txn = self.env["custom.ppob.transaction"].search(
            [("mitra_id", "=", self.mitra.id), ("idempotency_key", "=", notrx)], limit=1
        )
        self.assertEqual(txn.dynamic_field, {"userid": "9630001"})

    # ------------------------------------------------------------------
    # CALLBACK
    # ------------------------------------------------------------------

    def test_callback_fires_on_terminal(self):
        self.cred.callback_url = self.base_url() + "/web/health"
        notrx = "SELL-CB-1"
        p = {"user": "ERAUSER", "produk": self.product.code, "mdn": "0812", "notrx": notrx}
        p["signature"] = _md5(p["mdn"] + p["produk"] + notrx + _md5(self.password))
        self._post_form("/pps/sell", p)
        txn = self.env["custom.ppob.transaction"].search(
            [("mitra_id", "=", self.mitra.id), ("idempotency_key", "=", notrx)], limit=1
        )
        self.assertEqual(txn.pps_callback_state, "pending")
        self.env["custom.ppob.transaction"]._cron_pps_dispatch_callbacks()
        txn.invalidate_recordset()
        self.assertEqual(txn.pps_callback_state, "sent")
        log = self.env["custom.ppob.pps.callback.log"].search([("transaction_id", "=", txn.id)])
        self.assertTrue(log and log[0].ok)
