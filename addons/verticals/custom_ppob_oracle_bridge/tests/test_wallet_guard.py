# -*- coding: utf-8 -*-
"""Tests for the wallet mirror guard."""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import OracleBridgeCommon


@tagged("post_install", "-at_install", "custom_ppob_oracle_bridge")
class TestWalletGuard(OracleBridgeCommon):
    def _make_wallet(self, mirror_source="oracle", balance=1000000.0):
        return self.env["custom.ppob.wallet"].create(
            {
                "partner_id": self.partner.id,
                "class_id": self.product_class.id,
                "mirror_source": mirror_source,
                "balance": balance,
                "account_id": self.env["account.account"]
                .search([("account_type", "in", ["liability_current", "liability_payable"])], limit=1)
                .id,
                "journal_id": self._journal(self.__class__).id,
            }
        )

    def test_native_debit_blocked_on_oracle_mirror(self):
        wallet = self._make_wallet(mirror_source="oracle")
        counterpart = self.env["account.account"].search([("account_type", "=", "expense")], limit=1)
        with self.assertRaises(UserError) as ctx:
            wallet._atomic_debit(
                amount=100.0,
                reason="Native sale (should fail)",
                counterpart_account=counterpart,
                move_type="sale",
            )
        self.assertIn("Oracle", str(ctx.exception))

    def test_native_credit_blocked_on_oracle_mirror(self):
        wallet = self._make_wallet(mirror_source="oracle")
        counterpart = self.env["account.account"].search([("account_type", "=", "income")], limit=1)
        with self.assertRaises(UserError):
            wallet._atomic_credit(
                amount=100.0,
                reason="Native topup (should fail)",
                counterpart_account=counterpart,
                move_type="topup",
            )

    def test_native_wallet_unaffected(self):
        wallet = self._make_wallet(mirror_source="native")
        self.assertEqual(wallet.mirror_source, "native")
        wallet._check_oracle_mirror_guard("sale")  # should not raise
