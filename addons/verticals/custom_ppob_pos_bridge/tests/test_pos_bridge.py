# -*- coding: utf-8 -*-
"""End-to-end tests for the POS mirror bridge (model-level ingest).

Drives ``custom.ppob.pos.txn._ingest_event`` directly (exactly what the
two HTTP controllers call after auth), so the two-feed join + GL projection +
idempotency + skipped queue + mirror guard are all exercised without HTTP.
Reuses the TELKO product class seeded by ``custom_ppob_core`` post_init.
"""

import hashlib
import hmac
import json
import time
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install", "custom_ppob_pos_bridge")
class TestPosBridge(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Join = cls.env["custom.ppob.pos.txn"]
        cls.klass = cls.env["custom.ppob.product.class"].search([("code", "=", "TELKO")], limit=1)
        assert cls.klass, "TELKO class should be seeded by custom_ppob_core post_init"

        cls.mitra = cls.env["res.partner"].create(
            {
                "name": "Mitra POS",
                "x_custom_ppob_is_mitra": True,
                "x_custom_ppob_mitra_code": "ERA-MTR-1",
            }
        )
        cls.vendor = cls.env["res.partner"].create(
            {
                "name": "Biller POS",
                "x_custom_ppob_is_provider": True,
            }
        )
        cls.product = cls.env["custom.ppob.product"].create(
            {
                "code": "ERA_TSEL5",
                "name": "TSEL 5k (pos)",
                "class_id": cls.klass.id,
                "denom": 5000.0,
                "cost_price_default": 4900.0,
            }
        )
        cls.provider = cls.env["custom.ppob.provider"].create(
            {
                "code": "ERABILL",
                "name": "POS Biller",
                "partner_id": cls.vendor.id,
                "settlement_mode": "postpaid",
                "adapter_class": "ppob_mock",
            }
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _pos(self, ref, status="success", event_type="sale", sell=5000.0, **extra):
        payload = {
            "pos_trx_ref": ref,
            "event_type": event_type,
            "status": status,
            "mitra_ref": "ERA-MTR-1",
            "product_code": self.product.code,
            "sell_price": sell,
            "customer_no": "0812xxxx8900",
            "wallet_balance_after": 95000.0,
            "txn_time": fields.Datetime.to_string(fields.Datetime.now()),
        }
        payload.update(extra)
        return self.Join._ingest_event("pos", payload)

    def _h2h(self, ref, status="success", cost=4900.0, **extra):
        payload = {
            "pos_trx_ref": ref,
            "status": status,
            "biller_code": self.provider.code,
            "product_code": self.product.code,
            "cost_price": cost,
            "serial": "SN-123",
            "h2h_trx_id": "H2H-9",
            "deposit_balance_after": 500000.0,
        }
        payload.update(extra)
        return self.Join._ingest_event("h2h", payload)

    def _wallet(self):
        return self.env["custom.ppob.wallet"].search(
            [
                ("partner_id", "=", self.mitra.id),
                ("class_id", "=", self.klass.id),
            ],
            limit=1,
        )

    # ------------------------------------------------------------------
    # POS feed
    # ------------------------------------------------------------------

    def test_pos_sale_projects_revenue_and_drawdown(self):
        join = self._pos("POS-1")
        self.assertTrue(join)
        self.assertEqual(join.match_state, "pos_only")
        self.assertTrue(join.pos_posted)

        wallet = self._wallet()
        self.assertTrue(wallet.pos_mirror)
        # Mirror drawdown from 0 with no ceiling -> -5000.
        self.assertAlmostEqual(wallet.balance, -5000.0, places=2)
        wm = self.env["custom.ppob.wallet.move"].search([("wallet_id", "=", wallet.id), ("type", "=", "pos_sale")])
        self.assertEqual(len(wm), 1)
        self.assertTrue(wm.move_id, "wallet move must carry a posted GL entry")
        self.assertEqual(wm.move_id.state, "posted")

        # Mirror container transaction feeds the daily rollup faktur.
        txn = join.transaction_id
        self.assertTrue(txn)
        self.assertEqual(txn.state, "success")
        self.assertEqual(txn.pos_txn_id, join)
        self.assertAlmostEqual(txn.sell_price, 5000.0, places=2)

    def test_pos_topup_credits_wallet(self):
        join = self._pos("POS-TU", event_type="topup", sell=100000.0)
        self.assertTrue(join)
        wallet = self._wallet()
        self.assertAlmostEqual(wallet.balance, 100000.0, places=2)
        wm = self.env["custom.ppob.wallet.move"].search(
            [("wallet_id", "=", wallet.id), ("type", "=", "pos_topup")]
        )
        self.assertEqual(len(wm), 1)

    def test_pos_failed_sale_posts_nothing(self):
        join = self._pos("POS-FAIL", status="failed")
        self.assertTrue(join)
        self.assertFalse(join.pos_posted)
        self.assertFalse(self._wallet(), "no wallet created for a failed sale")

    # ------------------------------------------------------------------
    # H2H feed + join
    # ------------------------------------------------------------------

    def test_h2h_projects_cogs_and_deposit(self):
        join = self._h2h("H2H-ONLY")
        self.assertEqual(join.match_state, "h2h_only")
        self.assertTrue(join.h2h_posted)
        self.assertTrue(join.h2h_move_id)
        self.assertEqual(join.h2h_move_id.state, "posted")
        # Dr COGS / Cr Deposit, balanced.
        self.assertAlmostEqual(sum(join.h2h_move_id.line_ids.mapped("debit")), 4900.0, 2)
        self.assertAlmostEqual(sum(join.h2h_move_id.line_ids.mapped("credit")), 4900.0, 2)

    def test_two_feeds_join_and_margin(self):
        self._pos("POS-JOIN")
        join = self._h2h("POS-JOIN")
        self.assertEqual(join.match_state, "matched")
        self.assertAlmostEqual(join.margin, 100.0, places=2)  # 5000 - 4900
        self.assertAlmostEqual(join.transaction_id.cost_price, 4900.0, places=2)

    def test_status_mismatch_flags_exception(self):
        self._pos("POS-MM")
        join = self._h2h("POS-MM", status="failed")
        self.assertEqual(join.match_state, "mismatch")

    # ------------------------------------------------------------------
    # Idempotency
    # ------------------------------------------------------------------

    def test_pos_ingest_is_idempotent(self):
        self._pos("POS-IDEM")
        wallet = self._wallet()
        bal_after_first = wallet.balance
        moves_first = self.env["custom.ppob.wallet.move"].search_count([("wallet_id", "=", wallet.id)])
        # Replay the identical POS event.
        join2 = self._pos("POS-IDEM")
        self.assertTrue(join2)
        self.assertAlmostEqual(self._wallet().balance, bal_after_first, places=2)
        self.assertEqual(
            self.env["custom.ppob.wallet.move"].search_count([("wallet_id", "=", wallet.id)]),
            moves_first,
            "duplicate POS event must not double-post",
        )

    # ------------------------------------------------------------------
    # Mapping failures -> skipped queue
    # ------------------------------------------------------------------

    def test_unmapped_mitra_is_skipped(self):
        payload = {
            "pos_trx_ref": "POS-NOMITRA",
            "event_type": "sale",
            "status": "success",
            "mitra_ref": "DOES-NOT-EXIST",
            "product_code": self.product.code,
            "sell_price": 5000.0,
        }
        result = self.Join._ingest_event("pos", payload)
        self.assertFalse(result)
        skip = self.env["custom.ppob.pos.ingest.skipped"].search([("external_ref", "=", "POS-NOMITRA:pos")])
        self.assertEqual(len(skip), 1)
        self.assertEqual(skip.skip_reason, "mitra_not_mapped")

    def test_skipped_replay_after_mapping(self):
        payload = {
            "pos_trx_ref": "POS-REPLAY",
            "event_type": "sale",
            "status": "success",
            "mitra_ref": "ERA-NEW",
            "product_code": self.product.code,
            "sell_price": 5000.0,
        }
        self.assertFalse(self.Join._ingest_event("pos", payload))
        skip = self.env["custom.ppob.pos.ingest.skipped"].search([("external_ref", "=", "POS-REPLAY:pos")])
        self.assertTrue(skip)
        # Complete the mapping, then replay.
        self.mitra.copy({"name": "Mitra New", "x_custom_ppob_mitra_code": "ERA-NEW"})
        skip.action_replay()
        skip.invalidate_recordset()
        self.assertTrue(skip.replayed)
        self.assertTrue(skip.pos_txn_id)

    # ------------------------------------------------------------------
    # Mirror guard
    # ------------------------------------------------------------------

    @mute_logger("odoo.sql_db")
    def test_native_mutation_blocked_on_mirror_wallet(self):
        wallet = self.env["custom.ppob.wallet"].create(
            {
                "partner_id": self.mitra.id,
                "class_id": self.klass.id,
                "pos_mirror": True,
            }
        )
        bank = self.env["custom.ppob.account.mapping"]._get_account("cash_bca_escrow", self.company)
        with self.assertRaises(UserError):
            wallet._atomic_debit(amount=100.0, reason="native", counterpart_account=bank, move_type="sale")
        # Mirror helper is allowed.
        wm = wallet._mirror_debit(amount=100.0, reason="mirror", counterpart_account=bank, move_type="pos_sale")
        self.assertTrue(wm)
        self.assertAlmostEqual(wallet.balance, -100.0, places=2)

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    def test_reconcile_flags_stale_pos_only(self):
        join = self._pos("POS-STALE")
        join.pos_txn_time = fields.Datetime.now() - timedelta(hours=2)
        self.Join._cron_pos_reconcile()
        join.invalidate_recordset()
        self.assertTrue(join.recon_flagged)
        self.assertIn("pos_only", join.recon_note or "")


@tagged("post_install", "-at_install", "custom_ppob_pos_bridge")
class TestPosIngestHttp(HttpCase):
    """Exercise the real HMAC ingest endpoints over HTTP.

    The model-level tests above bypass the controller; this class covers the
    auth + write path (Odoo 19 auth='none' routes are readonly and have no
    env.user/company -- the endpoints must be auth='public' + readonly=False).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.secret = "pos-secret"
        cls.env["ir.config_parameter"].sudo().set_param("pos.pos.secret", cls.secret)
        cls.env["custom.ppob.pos.connection"].create(
            {
                "feed": "pos",
                "credential_ref": "pos.pos.secret",
                "status": "active",
            }
        )
        cls.klass = cls.env["custom.ppob.product.class"].search([("code", "=", "TELKO")], limit=1)
        cls.mitra = cls.env["res.partner"].create(
            {
                "name": "Mitra HTTP",
                "x_custom_ppob_is_mitra": True,
                "x_custom_ppob_mitra_code": "ERA-HTTP-1",
            }
        )
        cls.product = cls.env["custom.ppob.product"].create(
            {
                "code": "ERA_HTTP5",
                "name": "TSEL 5k http",
                "class_id": cls.klass.id,
                "denom": 5000.0,
                "cost_price_default": 4900.0,
            }
        )

    def _signed_post(self, path, payload):
        body = json.dumps(payload).encode()
        ts = str(int(time.time()))
        sig = hmac.new(self.secret.encode(), ts.encode() + body, hashlib.sha256).hexdigest()
        return self.url_open(
            path,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Signature": sig,
                "X-Timestamp": ts,
                "X-Odoo-Database": self.env.cr.dbname,
            },
            timeout=30,
        )

    def test_pos_feed_http_posts_gl(self):
        payload = {
            "pos_trx_ref": "HTTP-POS-1",
            "event_type": "sale",
            "status": "success",
            "mitra_ref": "ERA-HTTP-1",
            "product_code": self.product.code,
            "sell_price": 5000.0,
            "customer_no": "0812xxxx1111",
        }
        resp = self._signed_post("/api/ppob/pos/pos", payload)
        self.assertEqual(resp.status_code, 200, resp.text[:300])
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["posted"], body)
        join = self.env["custom.ppob.pos.txn"].search([("pos_trx_ref", "=", "HTTP-POS-1")], limit=1)
        self.assertTrue(join.pos_posted)
        self.assertTrue(join.pos_wallet_move_id.move_id, "GL entry must be posted")

    def test_bad_signature_rejected(self):
        body = json.dumps({"pos_trx_ref": "HTTP-BAD"}).encode()
        resp = self.url_open(
            "/api/ppob/pos/pos",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Signature": "deadbeef",
                "X-Timestamp": str(int(time.time())),
                "X-Odoo-Database": self.env.cr.dbname,
            },
            timeout=30,
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error_code"], "BAD_SIGNATURE")
