# -*- coding: utf-8 -*-
from psycopg2 import IntegrityError

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.custom_ppob_provider.models.ppob_provider_adapter_base import (
    AdapterResult,
    PPOBProviderAdapter,
    register_adapter,
)


@register_adapter("ppob_test_statusfail")
class _StatusFailAdapter(PPOBProviderAdapter):
    """Test adapter: pay() succeeds but status() reports a confirmed failure,
    so the reaper's status-confirmed-failure path can be exercised."""

    def inquiry(self, transaction):
        return AdapterResult(ok=True, provider_ref="TF-INQ", amount=transaction.sell_price)

    def pay(self, transaction):
        return AdapterResult(ok=True, provider_ref="TF-PAY", amount=transaction.cost_price)

    def status(self, provider_ref):
        return AdapterResult(
            ok=False, error_code="CONFIRMED_FAIL", error_message="provider says failed", raw={"state": "failed"}
        )

    def topup(self, amount):
        return AdapterResult(ok=True, amount=amount)


@tagged("post_install", "-at_install", "custom_ppob_sale")
class TestPpobSale(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        Mapping = cls.env["custom.ppob.account.mapping"]
        cls.klass = cls.env["custom.ppob.product.class"].search([("code", "=", "TELKO")], limit=1)
        assert cls.klass, "TELKO class should be seeded by custom_ppob_core post_init"
        cls.bank = Mapping._get_account("cash_bca_escrow", cls.company)

        cls.mitra = cls.env["res.partner"].create(
            {
                "name": "Mitra Test",
                "x_custom_ppob_is_mitra": True,
                "x_custom_ppob_mitra_code": "MTRTEST1",
            }
        )
        cls.vendor = cls.env["res.partner"].create(
            {
                "name": "Vendor Test",
                "x_custom_ppob_is_provider": True,
            }
        )
        cls.product = cls.env["custom.ppob.product"].create(
            {
                "code": "TSELSALE5",
                "name": "TSEL Pulsa 5k (sale test)",
                "class_id": cls.klass.id,
                "denom": 5000.0,
                "cost_price_default": 4900.0,
            }
        )
        # Mitra wallet, topped up.
        cls.wallet = cls.env["custom.ppob.wallet"].create(
            {
                "partner_id": cls.mitra.id,
                "class_id": cls.klass.id,
            }
        )
        cls.wallet._atomic_credit(
            amount=1_000_000.0,
            reason="seed",
            counterpart_account=cls.bank,
            move_type="topup",
        )

    def _make_provider(self, code, adapter="ppob_mock", priority=100, mock_outcome="success"):
        provider = self.env["custom.ppob.provider"].create(
            {
                "code": code,
                "name": f"Provider {code}",
                "partner_id": self.vendor.id,
                "settlement_mode": "prepaid_deposit",
                "bucket_mode": "bulky",
                "adapter_class": adapter,
                "mock_outcome": mock_outcome,
                "failover_priority": priority,
            }
        )
        provider.action_ensure_buckets()
        provider.bucket_ids._atomic_credit(
            dpp_amount=500_000.0,
            tax_amount=0.0,
            gross_amount=500_000.0,
            reason="seed bucket",
            counterpart_account=self.bank,
            move_type="topup",
        )
        self.env["custom.ppob.provider.sku.map"].create(
            {
                "provider_id": provider.id,
                "product_id": self.product.id,
                "provider_sku": f"SKU-{code}",
                "buy_price": 4900.0,
                "priority": priority,
            }
        )
        return provider

    def _make_txn(self, provider=None, sell=5000.0, key=None):
        return self.env["custom.ppob.transaction"].create(
            {
                "mitra_id": self.mitra.id,
                "product_id": self.product.id,
                "msisdn": "081200000000",
                "provider_id": provider.id if provider else False,
                "sell_price": sell,
                "cost_price": 4900.0,
                "idempotency_key": key or f"K-{sell}-{id(self)}",
            }
        )

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_dispatch_success_debits_and_posts(self):
        provider = self._make_provider("SALE_OK")
        wallet_before = self.wallet.balance
        bucket_before = provider.bucket_ids.balance
        txn = self._make_txn(provider, key="K-OK")
        txn.action_dispatch()

        self.assertEqual(txn.state, "success")
        self.assertTrue(txn.provider_ref)
        self.assertAlmostEqual(self.wallet.balance, wallet_before - 5000.0, places=2)
        self.assertAlmostEqual(provider.bucket_ids.balance, bucket_before - 4900.0, places=2)
        # Each money leg posts exactly one balanced GL move.
        self.assertTrue(txn.wallet_move_id.move_id)
        self.assertTrue(txn.bucket_move_id.move_id)
        for move in (txn.wallet_move_id.move_id, txn.bucket_move_id.move_id):
            self.assertAlmostEqual(sum(move.line_ids.mapped("debit")), sum(move.line_ids.mapped("credit")), places=2)
        # Subledger back-references wired.
        self.assertEqual(txn.wallet_move_id.ppob_transaction_id, txn)
        self.assertEqual(txn.bucket_move_id.ppob_transaction_id, txn)

    def test_idempotency_key_unique_per_mitra(self):
        provider = self._make_provider("SALE_IDEM")
        self._make_txn(provider, key="DUP")
        with self.assertRaises(IntegrityError), self.cr.savepoint(), mute_logger("odoo.sql_db"):
            self._make_txn(provider, key="DUP")
            self.env.flush_all()

    # ------------------------------------------------------------------
    # Failure + refund reversal
    # ------------------------------------------------------------------

    def test_dispatch_fail_refunds_subledgers(self):
        provider = self._make_provider("SALE_FAIL", mock_outcome="fail")
        wallet_before = self.wallet.balance
        bucket_before = provider.bucket_ids.balance
        txn = self._make_txn(provider, key="K-FAIL")
        txn.action_dispatch()

        self.assertEqual(txn.state, "failed")
        # Wallet + bucket restored to pre-dispatch balances (net zero).
        self.assertAlmostEqual(self.wallet.balance, wallet_before, places=2)
        self.assertAlmostEqual(provider.bucket_ids.balance, bucket_before, places=2)
        self.assertTrue(txn.wallet_refund_move_id)
        self.assertTrue(txn.bucket_refund_move_id)

    def test_refund_is_idempotent(self):
        provider = self._make_provider("SALE_FAIL2", mock_outcome="fail")
        txn = self._make_txn(provider, key="K-FAIL2")
        txn.action_dispatch()
        first_refund = txn.wallet_refund_move_id
        # Manual re-refund must not double-credit.
        txn._refund_subledgers()
        self.assertEqual(txn.wallet_refund_move_id, first_refund)

    # ------------------------------------------------------------------
    # Failover routing
    # ------------------------------------------------------------------

    def test_failover_prefers_lower_priority(self):
        low = self._make_provider("SALE_P10", priority=10)
        self._make_provider("SALE_P50", priority=50)
        txn = self._make_txn(provider=False, key="K-FO")  # auto-route
        txn.action_dispatch()
        self.assertEqual(txn.provider_id, low)

    # ------------------------------------------------------------------
    # Caps
    # ------------------------------------------------------------------

    def test_daily_cap_blocks(self):
        self.mitra.x_custom_ppob_daily_txn_cap = 4000.0  # below one 5000 sale
        provider = self._make_provider("SALE_CAP")
        txn = self._make_txn(provider, sell=5000.0, key="K-CAP")
        with self.assertRaises(UserError):
            txn.action_dispatch()

    # ------------------------------------------------------------------
    # PMK-63/2022 margin VAT math (all four vat modes)
    # ------------------------------------------------------------------

    def _class(self, code, vat_mode):
        return self.env["custom.ppob.product.class"].create(
            {
                "code": code,
                "name": code,
                "vat_mode": vat_mode,
            }
        )

    def _txn_for_mode(self, vat_mode, sell, cost):
        klass = self._class(f"VC_{vat_mode}", vat_mode)
        product = self.env["custom.ppob.product"].create(
            {
                "code": f"P_{vat_mode}",
                "name": vat_mode,
                "class_id": klass.id,
                "denom": sell,
            }
        )
        return self.env["custom.ppob.transaction"].create(
            {
                "mitra_id": self.mitra.id,
                "product_id": product.id,
                "msisdn": "08120000",
                "sell_price": sell,
                "cost_price": cost,
                "idempotency_key": f"VAT-{vat_mode}",
            }
        )

    def test_vat_margin(self):
        t = self._txn_for_mode("margin", 5000.0, 4900.0)
        self.assertAlmostEqual(t.dpp_amount, 100.0, places=2)  # sell - cost
        self.assertAlmostEqual(t.ppn_amount, 11.0, places=2)  # 100 * 0.11

    def test_vat_other_valuation(self):
        t = self._txn_for_mode("other_valuation", 11000.0, 10000.0)
        self.assertAlmostEqual(t.dpp_amount, 10000.0, places=2)  # 10/11 * sell
        self.assertAlmostEqual(t.ppn_amount, 1100.0, places=2)  # 10000 * 0.11

    def test_vat_gross(self):
        t = self._txn_for_mode("gross", 5000.0, 4900.0)
        self.assertAlmostEqual(t.dpp_amount, 5000.0, places=2)
        self.assertAlmostEqual(t.ppn_amount, 550.0, places=2)

    def test_vat_exempt(self):
        t = self._txn_for_mode("exempt", 5000.0, 4900.0)
        self.assertAlmostEqual(t.dpp_amount, 0.0, places=2)
        self.assertAlmostEqual(t.ppn_amount, 0.0, places=2)

    # ------------------------------------------------------------------
    # Retry clone
    # ------------------------------------------------------------------

    def test_retry_clone_family(self):
        provider = self._make_provider("SALE_RETRY", mock_outcome="fail")
        txn = self._make_txn(provider, key="K-RETRY")
        txn.action_dispatch()
        self.assertEqual(txn.state, "failed")
        action = txn.action_retry()
        clone = self.env["custom.ppob.transaction"].browse(action["res_id"])
        self.assertEqual(clone.state, "pending")
        self.assertEqual(clone.attempt_no, txn.attempt_no + 1)
        self.assertIn("/R", clone.idempotency_key)

    # ------------------------------------------------------------------
    # Reaper
    # ------------------------------------------------------------------

    def test_reaper_status_success_marks_success(self):
        provider = self._make_provider("SALE_REAP_OK")
        txn = self._make_txn(provider, key="K-REAP-OK")
        txn.action_dispatch()
        # Force it back to in_progress + old dispatch time to look stale.
        txn.write({"state": "in_progress"})
        self.env.flush_all()  # persist state before the reaper's search
        self.env.cr.execute(
            "UPDATE custom_ppob_transaction SET dispatched_at = now() - interval '1 hour' WHERE id = %s",
            (txn.id,),
        )
        txn.invalidate_recordset(["dispatched_at"])
        self.env["custom.ppob.transaction"]._cron_reap_stale_inprogress()
        self.assertEqual(txn.state, "success")  # mock status() confirms success

    def test_reaper_status_fail_refunds_and_timeouts(self):
        provider = self._make_provider("SALE_REAP_FAIL", adapter="ppob_test_statusfail")
        wallet_before = self.wallet.balance
        bucket_before = provider.bucket_ids.balance
        txn = self._make_txn(provider, key="K-REAP-FAIL")
        txn.action_dispatch()  # pay ok -> in_progress -> success (pay ok=True) ...
        # pay() ok=True marks success; force in_progress to test the reaper path.
        txn.write({"state": "in_progress", "wallet_refund_move_id": False, "bucket_refund_move_id": False})
        self.env.flush_all()  # persist state before the reaper's search
        self.env.cr.execute(
            "UPDATE custom_ppob_transaction SET dispatched_at = now() - interval '1 hour' WHERE id = %s",
            (txn.id,),
        )
        txn.invalidate_recordset(["dispatched_at"])
        self.env["custom.ppob.transaction"]._cron_reap_stale_inprogress()
        self.assertEqual(txn.state, "timeout")
        # Status confirmed failure -> subledgers refunded.
        self.assertAlmostEqual(self.wallet.balance, wallet_before, places=2)
        self.assertAlmostEqual(provider.bucket_ids.balance, bucket_before, places=2)
