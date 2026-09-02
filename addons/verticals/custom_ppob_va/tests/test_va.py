# -*- coding: utf-8 -*-
import hashlib
import hmac
import json
import time

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install", "custom_ppob_va")
class TestVaH2H(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.secret = "dummy-va-callback-secret"
        cls.env["ir.config_parameter"].sudo().set_param("custom_ppob.va.bca.secret", cls.secret)
        cls.env["custom.ppob.va.bank.connection"].create(
            {
                "name": "BCA Test",
                "bank_code": "BCA",
                "status": "active",
                "credential_ref": "custom_ppob.va.bca.secret",
                "max_clock_skew_s": 300,
                # No ip_whitelist -> all IPs allowed (test client is 127.0.0.1).
            }
        )
        klass = cls.env["custom.ppob.product.class"].search([("code", "=", "TELKO")], limit=1)
        cls.mitra = cls.env["res.partner"].create(
            {
                "name": "VA Mitra",
                "x_custom_ppob_is_mitra": True,
                "x_custom_ppob_mitra_code": "MTRVA1",
            }
        )
        cls.wallet = cls.env["custom.ppob.wallet"].create(
            {
                "partner_id": cls.mitra.id,
                "class_id": klass.id,
            }
        )
        cls.va = cls.env["custom.ppob.va.account"].create(
            {
                "mitra_id": cls.mitra.id,
                "wallet_id": cls.wallet.id,
                "bank_code": "BCA",
                "va_number": "1234500001",
                "mode": "h2h",
            }
        )

    def _sign(self, body, ts=None):
        ts = ts or str(int(time.time()))
        sig = hmac.new(self.secret.encode(), ts.encode() + body, hashlib.sha256).hexdigest()
        return ts, sig

    def _pay(self, payload, ts=None, sig=None):
        body = json.dumps(payload).encode()
        _ts, _sig = self._sign(body, ts)
        headers = {
            "Content-Type": "application/json",
            "X-Timestamp": ts or _ts,
            "X-Signature": sig if sig is not None else _sig,
        }
        return self.url_open("/api/ppob/va/BCA/payment", data=body, headers=headers)

    def test_valid_payment_credits_wallet_once(self):
        before = self.wallet.balance
        resp = self._pay({"va_number": "1234500001", "amount": 100000, "bank_ref": "TRX-001"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.wallet.invalidate_recordset(["balance"])
        self.assertAlmostEqual(self.wallet.balance, before + 100000, places=2)

    def test_bad_signature_rejected(self):
        resp = self._pay({"va_number": "1234500001", "amount": 5000, "bank_ref": "TRX-BAD"}, sig="deadbeef")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error_code"], "BAD_SIGNATURE")
        self.assertFalse(self.env["custom.ppob.va.topup"].search([("bank_ref", "=", "TRX-BAD")]))

    def test_expired_timestamp_rejected(self):
        old_ts = str(int(time.time()) - 10000)
        resp = self._pay({"va_number": "1234500001", "amount": 5000, "bank_ref": "TRX-OLD"}, ts=old_ts)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error_code"], "BAD_SIGNATURE")

    def test_replay_same_ts_sig_rejected(self):
        body = json.dumps({"va_number": "1234500001", "amount": 7000, "bank_ref": "TRX-RE1"}).encode()
        ts, sig = self._sign(body)
        headers = {"Content-Type": "application/json", "X-Timestamp": ts, "X-Signature": sig}
        r1 = self.url_open("/api/ppob/va/BCA/payment", data=body, headers=headers)
        self.assertEqual(r1.status_code, 200)
        # Exact same (ts, sig) -> nonce replay guard rejects.
        r2 = self.url_open("/api/ppob/va/BCA/payment", data=body, headers=headers)
        self.assertEqual(r2.status_code, 401)

    def test_duplicate_bank_ref_credits_once(self):
        before = self.wallet.balance
        r1 = self._pay({"va_number": "1234500001", "amount": 25000, "bank_ref": "TRX-DUP"})
        self.assertTrue(r1.json()["ok"])
        # Fresh signature (new timestamp), same bank_ref -> idempotent.
        time.sleep(1)
        r2 = self._pay({"va_number": "1234500001", "amount": 25000, "bank_ref": "TRX-DUP"})
        self.assertTrue(r2.json().get("duplicate"))
        self.wallet.invalidate_recordset(["balance"])
        self.assertAlmostEqual(self.wallet.balance, before + 25000, places=2)
        self.assertEqual(len(self.env["custom.ppob.va.topup"].search([("bank_ref", "=", "TRX-DUP")])), 1)

    def test_va_match_reconcile_rule(self):
        # Build a bank statement line and a va_match rule; assert it creates a topup.
        journal = self.env["account.journal"].search([("type", "=", "bank")], limit=1)
        if not journal:
            journal = self.env["account.journal"].create({"name": "Test Bank", "type": "bank", "code": "TBNK"})
        rule = self.env["custom.reconcile.rule"].create(
            {
                "name": "VA BCA match",
                "rule_type": "va_match",
                "va_bank_code": "BCA",
                "va_extract_regex": r"VA\s+(?P<va>\d+)",
            }
        )
        stmt = self.env["account.bank.statement.line"].create(
            {
                "journal_id": journal.id,
                "payment_ref": "VA 1234500001 topup",
                "amount": 50000.0,
                "date": "2026-07-15",
            }
        )
        topup = rule._apply_va_match(stmt)
        self.assertTrue(topup)
        self.assertEqual(topup.va_account_id, self.va)
        self.assertEqual(topup.state, "credited")
