# -*- coding: utf-8 -*-
from psycopg2 import IntegrityError

from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install", "custom_ppob_provider")
class TestProviderBucket(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company

        cls.partner_vendor = cls.env["res.partner"].create(
            {
                "name": "Test Provider Vendor",
                "x_custom_ppob_is_provider": True,
            }
        )

        cls.asset_account = cls.env["account.account"].search(
            [
                ("account_type", "=", "asset_current"),
            ],
            limit=1,
        ) or cls.env["account.account"].create(
            {
                "code": "TST.AC.1",
                "name": "Test Asset Current",
                "account_type": "asset_current",
                "company_ids": [Command.link(cls.company.id)],
            }
        )
        cls.input_vat_account = cls.env["account.account"].create(
            {
                "code": "TST.AC.VAT",
                "name": "Test PPN Masukan",
                "account_type": "asset_current",
                "company_ids": [Command.link(cls.company.id)],
            }
        )
        cls.bank_account = cls.env["account.account"].search(
            [
                ("account_type", "=", "asset_cash"),
            ],
            limit=1,
        ) or cls.env["account.account"].create(
            {
                "code": "TST.AC.CASH",
                "name": "Test Bank",
                "account_type": "asset_cash",
                "company_ids": [Command.link(cls.company.id)],
            }
        )
        cls.cogs_account = cls.env["account.account"].search(
            [
                ("account_type", "=", "expense_direct_cost"),
            ],
            limit=1,
        ) or cls.env["account.account"].create(
            {
                "code": "TST.AC.COGS",
                "name": "Test COGS",
                "account_type": "expense_direct_cost",
                "company_ids": [Command.link(cls.company.id)],
            }
        )

        cls.journal = cls.env["account.journal"].search(
            [
                ("type", "=", "general"),
                ("company_id", "=", cls.company.id),
            ],
            limit=1,
        ) or cls.env["account.journal"].create(
            {
                "name": "Test General",
                "type": "general",
                "code": "TGEN",
                "company_id": cls.company.id,
            }
        )

        cls.product_class = cls.env["custom.ppob.product.class"].search([], limit=1) or cls.env[
            "custom.ppob.product.class"
        ].create(
            {
                "code": "TST",
                "name": "Test Class",
            }
        )
        cls.product_5k = cls.env["custom.ppob.product"].create(
            {
                "code": "TSEL5KTEST",
                "name": "Pulsa TSEL 5k (test)",
                "class_id": cls.product_class.id,
                "denom": 5000.0,
                "cost_price_default": 4900.0,
            }
        )
        cls.product_10k = cls.env["custom.ppob.product"].create(
            {
                "code": "TSEL10KTEST",
                "name": "Pulsa TSEL 10k (test)",
                "class_id": cls.product_class.id,
                "denom": 10000.0,
                "cost_price_default": 9800.0,
            }
        )

    def _make_provider(self, code, mode="bulky"):
        return self.env["custom.ppob.provider"].create(
            {
                "code": code,
                "name": f"Provider {code}",
                "partner_id": self.partner_vendor.id,
                "settlement_mode": "prepaid_deposit",
                "bucket_mode": mode,
                "tax_rate_topup": 0.11,
                "journal_id": self.journal.id,
                "bucket_inventory_account_id": self.asset_account.id,
                "input_vat_account_id": self.input_vat_account.id,
            }
        )

    # ------------------------------------------------------------------
    # Topup / debit semantics
    # ------------------------------------------------------------------

    def test_atomic_credit_bulky_split_journal(self):
        provider = self._make_provider("TBULKY1")
        provider.action_ensure_buckets()
        bucket = provider.bucket_ids
        self.assertEqual(len(bucket), 1)
        self.assertEqual(bucket.mode, "bulky")

        gross = 50_000_000.0
        rate = 0.11
        dpp = round(gross / (1.0 + rate), 2)
        tax = round(gross - dpp, 2)
        bucket._atomic_credit(
            dpp_amount=dpp,
            tax_amount=tax,
            gross_amount=gross,
            reason="Test topup",
            counterpart_account=self.bank_account,
            move_type="topup",
        )
        self.assertAlmostEqual(bucket.balance, dpp, places=2)

        bm = bucket.move_ids
        self.assertEqual(len(bm), 1)
        self.assertEqual(bm.type, "topup")
        self.assertAlmostEqual(bm.tax_amount, tax, places=2)
        self.assertAlmostEqual(bm.gross_amount, gross, places=2)

        # 3-line journal: Dr bucket / Dr PPN / Cr cash
        line_ids = bm.move_id.line_ids
        self.assertEqual(len(line_ids), 3)
        self.assertAlmostEqual(sum(line_ids.mapped("debit")), gross, places=2)
        self.assertAlmostEqual(sum(line_ids.mapped("credit")), gross, places=2)
        bucket_line = line_ids.filtered(lambda l: l.account_id == self.asset_account)
        vat_line = line_ids.filtered(lambda l: l.account_id == self.input_vat_account)
        self.assertAlmostEqual(bucket_line.debit, dpp, places=2)
        self.assertAlmostEqual(vat_line.debit, tax, places=2)

    def test_atomic_debit_fixed_uses_correct_bucket(self):
        provider = self._make_provider("TFIXED1", mode="fixed_denom")
        for prod in (self.product_5k, self.product_10k):
            self.env["custom.ppob.provider.sku.map"].create(
                {
                    "provider_id": provider.id,
                    "product_id": prod.id,
                    "provider_sku": f"PSKU-{prod.code}",
                    "buy_price": prod.cost_price_default,
                }
            )
        provider.action_ensure_buckets()
        b5k = provider.bucket_ids.filtered(lambda b: b.product_id == self.product_5k)
        b10k = provider.bucket_ids.filtered(lambda b: b.product_id == self.product_10k)
        self.assertTrue(b5k and b10k)

        b5k._atomic_credit(
            dpp_amount=100_000.0,
            tax_amount=11_000.0,
            gross_amount=111_000.0,
            reason="topup 5k",
            counterpart_account=self.bank_account,
            move_type="topup",
        )
        b10k._atomic_credit(
            dpp_amount=200_000.0,
            tax_amount=22_000.0,
            gross_amount=222_000.0,
            reason="topup 10k",
            counterpart_account=self.bank_account,
            move_type="topup",
        )

        self.assertEqual(provider._resolve_bucket_for(self.product_5k), b5k)
        self.assertEqual(provider._resolve_bucket_for(self.product_10k), b10k)

        b10k._atomic_debit(
            amount=9_800.0,
            reason="sale TSEL10K",
            counterpart_account=self.cogs_account,
            move_type="usage",
        )
        self.assertAlmostEqual(b5k.balance, 100_000.0, places=2)
        self.assertAlmostEqual(b10k.balance, 200_000.0 - 9_800.0, places=2)

    def test_insufficient_balance_raises(self):
        provider = self._make_provider("TINS")
        provider.action_ensure_buckets()
        bucket = provider.bucket_ids
        with self.assertRaises(UserError):
            bucket._atomic_debit(
                amount=1.0,
                reason="nope",
                counterpart_account=self.cogs_account,
                move_type="usage",
            )

    def test_resolver_no_match_raises(self):
        provider = self._make_provider("TRES", mode="fixed_denom")
        with self.assertRaises(UserError):
            provider._resolve_bucket_for(self.product_5k)

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    def test_partial_unique_fixed_denom(self):
        provider = self._make_provider("TUNIQ1", mode="fixed_denom")
        self.env["custom.ppob.provider.bucket"].create(
            {
                "provider_id": provider.id,
                "mode": "fixed_denom",
                "product_id": self.product_5k.id,
                "account_id": self.asset_account.id,
                "journal_id": self.journal.id,
            }
        )
        with self.assertRaises(IntegrityError), self.cr.savepoint(), mute_logger("odoo.sql_db"):
            self.env["custom.ppob.provider.bucket"].create(
                {
                    "provider_id": provider.id,
                    "mode": "fixed_denom",
                    "product_id": self.product_5k.id,
                    "account_id": self.asset_account.id,
                    "journal_id": self.journal.id,
                }
            )
            self.env.flush_all()

    def test_partial_unique_bulky(self):
        provider = self._make_provider("TUNIQ2", mode="bulky")
        self.env["custom.ppob.provider.bucket"].create(
            {
                "provider_id": provider.id,
                "mode": "bulky",
                "account_id": self.asset_account.id,
                "journal_id": self.journal.id,
            }
        )
        with self.assertRaises(IntegrityError), self.cr.savepoint(), mute_logger("odoo.sql_db"):
            self.env["custom.ppob.provider.bucket"].create(
                {
                    "provider_id": provider.id,
                    "mode": "bulky",
                    "account_id": self.asset_account.id,
                    "journal_id": self.journal.id,
                }
            )
            self.env.flush_all()

    def test_fixed_requires_product(self):
        provider = self._make_provider("TC1", mode="fixed_denom")
        with self.assertRaises(ValidationError):
            self.env["custom.ppob.provider.bucket"].create(
                {
                    "provider_id": provider.id,
                    "mode": "fixed_denom",
                    "account_id": self.asset_account.id,
                    "journal_id": self.journal.id,
                }
            )

    def test_bulky_rejects_product(self):
        provider = self._make_provider("TC2", mode="bulky")
        with self.assertRaises(ValidationError):
            self.env["custom.ppob.provider.bucket"].create(
                {
                    "provider_id": provider.id,
                    "mode": "bulky",
                    "product_id": self.product_5k.id,
                    "account_id": self.asset_account.id,
                    "journal_id": self.journal.id,
                }
            )

    def test_mode_switch_blocked_when_balance_nonzero(self):
        provider = self._make_provider("TMS", mode="bulky")
        provider.action_ensure_buckets()
        bucket = provider.bucket_ids
        bucket._atomic_credit(
            dpp_amount=10_000.0,
            tax_amount=0.0,
            gross_amount=10_000.0,
            reason="topup",
            counterpart_account=self.bank_account,
            move_type="topup",
        )
        with self.assertRaises(ValidationError):
            provider.bucket_mode = "fixed_denom"
