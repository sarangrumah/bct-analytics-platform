# -*- coding: utf-8 -*-
"""Tests for OracleBridgeAdapter."""

from odoo.tests import tagged

from .common import OracleBridgeCommon


@tagged("post_install", "-at_install", "custom_ppob_oracle_bridge")
class TestOracleBridgeAdapter(OracleBridgeCommon):
    def test_pay_success_creates_msg016t(self):
        with self._patch_connection():
            adapter = self.provider._get_adapter()
            self.assertEqual(adapter.__class__.__name__, "OracleBridgeAdapter")
            txn = self._make_transaction("TXN-PAY-OK")
            result = adapter.pay(txn)
        self.assertTrue(result.ok)
        self.assertIsNotNone(result.provider_ref)
        self.assertEqual(int(result.provider_ref), self.mock.next_msg016t_id - 1)
        self.assertEqual(len(self.mock.sp_calls), 1)
        self.assertIn("SellWithDenom", self.mock.sp_calls[0]["sp"])
        self.assertEqual(self.mock.sp_calls[0]["params"]["kodevoucher"], "TSEL10")
        self.assertEqual(self.mock.sp_calls[0]["params"]["trxNumber"], "TXN-PAY-OK")

    def test_pay_member_not_mapped(self):
        self.member_map.unlink()
        with self._patch_connection():
            adapter = self.provider._get_adapter()
            txn = self._make_transaction("TXN-NOMAP")
            result = adapter.pay(txn)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "member_not_mapped")
        self.assertEqual(len(self.mock.sp_calls), 0)

    def test_pay_voucher_not_mapped(self):
        self.sku_map.oracle_kode_voucher = False
        with self._patch_connection():
            adapter = self.provider._get_adapter()
            txn = self._make_transaction("TXN-NOVOUCHER")
            result = adapter.pay(txn)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "voucher_not_mapped")

    def test_pay_sp_business_error(self):
        self.mock.next_sp_outcome = {"trxId": 0, "err": 1, "msg": "Deposit kurang"}
        with self._patch_connection():
            adapter = self.provider._get_adapter()
            txn = self._make_transaction("TXN-DEPOSIT")
            result = adapter.pay(txn)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "oracle_business_error")
        self.assertIn("Deposit kurang", result.error_message)

    def test_status_terminal_success(self):
        rid = self.mock.add_msg016t(12345, "TSEL10", "TXN-S1", status="D")
        with self._patch_connection():
            result = self.provider._get_adapter().status(str(rid))
        self.assertTrue(result.ok)
        self.assertEqual(result.provider_ref, str(rid))

    def test_status_terminal_failed(self):
        rid = self.mock.add_msg016t(12345, "TSEL10", "TXN-S2", status="C")
        with self._patch_connection():
            result = self.provider._get_adapter().status(str(rid))
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "oracle_provider_failed")

    def test_status_in_progress_returns_none(self):
        rid = self.mock.add_msg016t(12345, "TSEL10", "TXN-S3", status="S")
        with self._patch_connection():
            result = self.provider._get_adapter().status(str(rid))
        self.assertIsNone(result.ok)

    def test_status_not_found(self):
        with self._patch_connection():
            result = self.provider._get_adapter().status("99999999")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "msg016t_not_found")

    def test_dispatch_oracle_bridge_skips_wallet_debit(self):
        with self._patch_connection():
            txn = self._make_transaction("TXN-DISPATCH-OK")
            txn.action_dispatch()
        self.assertEqual(txn.state, "in_progress")
        self.assertTrue(txn.oracle_msg016t_id)
        self.assertFalse(txn.wallet_move_id, "No wallet move should be created in oracle_bridge mode")

    def test_dispatch_failure_no_refund_needed(self):
        self.mock.next_sp_outcome = {"trxId": 0, "err": 1, "msg": "Stock kosong"}
        with self._patch_connection():
            txn = self._make_transaction("TXN-DISPATCH-FAIL")
            txn.action_dispatch()
        self.assertEqual(txn.state, "failed")
        self.assertEqual(txn.error_code, "oracle_business_error")
        self.assertFalse(txn.wallet_refund_move_id)

    def test_idempotency_unique_key_per_mitra(self):
        from psycopg2 import IntegrityError
        from odoo.tools.misc import mute_logger

        with self._patch_connection():
            txn1 = self._make_transaction("TXN-DUP")
            txn1.action_dispatch()
            with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
                with self.env.cr.savepoint():
                    self._make_transaction("TXN-DUP")
                    self.env.flush_all()
