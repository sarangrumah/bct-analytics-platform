# -*- coding: utf-8 -*-
"""Tests for the 3 Oracle Bridge cron jobs."""

from odoo.tests import tagged

from ..models.constants import PARAM_INBOUND_CURSOR
from .common import OracleBridgeCommon


@tagged("post_install", "-at_install", "custom_ppob_oracle_bridge")
class TestCronStatusSync(OracleBridgeCommon):
    def test_in_progress_to_success_when_oracle_done(self):
        with self._patch_connection():
            txn = self._make_transaction("TXN-CRON-OK")
            txn.action_dispatch()
            self.assertEqual(txn.state, "in_progress")
            self.mock.msg016t[txn.oracle_msg016t_id]["status_ussd_2_provider"] = "D"
            self.env["custom.ppob.transaction"]._cron_oracle_sync_status()
        txn.invalidate_recordset()
        self.assertEqual(txn.state, "success")

    def test_in_progress_to_failed_when_oracle_cancelled(self):
        with self._patch_connection():
            txn = self._make_transaction("TXN-CRON-FAIL")
            txn.action_dispatch()
            self.mock.msg016t[txn.oracle_msg016t_id]["status_ussd_2_provider"] = "C"
            self.mock.msg016t[txn.oracle_msg016t_id]["message_result_exec_ussd"] = "Provider down"
            self.env["custom.ppob.transaction"]._cron_oracle_sync_status()
        txn.invalidate_recordset()
        self.assertEqual(txn.state, "failed")
        self.assertEqual(txn.error_code, "oracle_provider_failed")

    def test_in_progress_stays_when_not_terminal(self):
        with self._patch_connection():
            txn = self._make_transaction("TXN-CRON-WAIT")
            txn.action_dispatch()
            self.mock.msg016t[txn.oracle_msg016t_id]["status_ussd_2_provider"] = "S"
            self.env["custom.ppob.transaction"]._cron_oracle_sync_status()
        txn.invalidate_recordset()
        self.assertEqual(txn.state, "in_progress")


@tagged("post_install", "-at_install", "custom_ppob_oracle_bridge")
class TestCronInboundIngest(OracleBridgeCommon):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param(PARAM_INBOUND_CURSOR, "0")

    def test_ingest_creates_transaction(self):
        rid = self.mock.add_msg016t(12345, "TSEL10", "LEGACY-001", status="P")
        with self._patch_connection():
            self.env["custom.ppob.transaction"]._cron_oracle_inbound_ingest()
        txn = self.env["custom.ppob.transaction"].search([("idempotency_key", "=", "LEGACY-001")])
        self.assertEqual(len(txn), 1)
        self.assertEqual(txn.inbound_source, "oracle_legacy")
        self.assertEqual(txn.oracle_msg016t_id, rid)
        self.assertEqual(txn.state, "in_progress")

    def test_ingest_skips_unmapped_member(self):
        self.mock.add_msg016t(99999, "TSEL10", "LEGACY-NOMAP", status="P")
        with self._patch_connection():
            self.env["custom.ppob.transaction"]._cron_oracle_inbound_ingest()
        skipped = self.env["custom.ppob.oracle.ingest.skipped"].search([("trx_number_client", "=", "LEGACY-NOMAP")])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped.skip_reason, "member_not_mapped")

    def test_ingest_idempotent(self):
        self.mock.add_msg016t(12345, "TSEL10", "LEGACY-IDEM", status="D")
        with self._patch_connection():
            self.env["custom.ppob.transaction"]._cron_oracle_inbound_ingest()
        self.env["ir.config_parameter"].sudo().set_param(PARAM_INBOUND_CURSOR, "0")
        with self._patch_connection():
            self.env["custom.ppob.transaction"]._cron_oracle_inbound_ingest()
        txns = self.env["custom.ppob.transaction"].search([("idempotency_key", "=", "LEGACY-IDEM")])
        self.assertEqual(len(txns), 1)

    def test_ingest_advances_cursor(self):
        for i in range(3):
            self.mock.add_msg016t(12345, "TSEL10", f"LEGACY-CURSOR-{i}", status="P")
        with self._patch_connection():
            self.env["custom.ppob.transaction"]._cron_oracle_inbound_ingest()
        new_cursor = int(self.env["ir.config_parameter"].sudo().get_param(PARAM_INBOUND_CURSOR))
        self.assertGreater(new_cursor, 0)


@tagged("post_install", "-at_install", "custom_ppob_oracle_bridge")
class TestCronBalanceMirror(OracleBridgeCommon):
    def _create_oracle_wallet(self, balance=0.0):
        return self.env["custom.ppob.wallet"].create(
            {
                "partner_id": self.partner.id,
                "class_id": self.product_class.id,
                "mirror_source": "oracle",
                "balance": balance,
                "account_id": self.env["account.account"]
                .search([("account_type", "in", ["liability_current", "liability_payable"])], limit=1)
                .id,
                "journal_id": self._journal(self.__class__).id,
            }
        )

    def test_mirror_records_delta_on_change(self):
        wallet = self._create_oracle_wallet(balance=500000.0)
        self.mock.msg019t[12345]["deposit_balance"] = 750000.0
        with self._patch_connection():
            self.env["custom.ppob.transaction"]._cron_oracle_balance_mirror()
        wallet.invalidate_recordset()
        self.assertEqual(wallet.balance, 750000.0)
        moves = self.env["custom.ppob.wallet.move"].search(
            [("wallet_id", "=", wallet.id), ("type", "=", "oracle_sync")]
        )
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves.amount_signed, 250000.0)

    def test_mirror_no_op_when_in_sync(self):
        wallet = self._create_oracle_wallet(balance=1000000.0)
        self.mock.msg019t[12345]["deposit_balance"] = 1000000.0
        with self._patch_connection():
            self.env["custom.ppob.transaction"]._cron_oracle_balance_mirror()
        moves = self.env["custom.ppob.wallet.move"].search(
            [("wallet_id", "=", wallet.id), ("type", "=", "oracle_sync")]
        )
        self.assertEqual(len(moves), 0)
        self.assertTrue(self.member_map.last_balance_sync)
