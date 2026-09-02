# -*- coding: utf-8 -*-
"""Digiflazz adapter tests.

The HTTP layer is mocked throughout. These tests prove that the request shape,
the MD5 signature derivation and the response mapping match the PUBLISHED SPEC.
They prove nothing about the real Digiflazz server, which has never been called.
What they DO protect is the reasoning that is easy to break later: pending must
not refund, ref_id must never be regenerated, and the two age guards must hold.
"""

import hashlib
import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

_API_KEY = "test-api-key-123"
_USERNAME = "dfuser"

# Known-answer vectors for the two fixed-suffix signatures, i.e.
# md5(_USERNAME + _API_KEY + suffix) as Digiflazz defines it.
#
# Frozen rather than recomputed with hashlib in the assertion: recomputing it
# here would restate the implementation instead of pinning the wire format, so
# reversing the concatenation order in BOTH places would still pass. A literal
# digest is what a protocol conformance test should assert against.
_SIGN_DEPOSIT = "c08c792ff375c4a65b427e148ccee1fb"  # suffix "depo"
_SIGN_PRICELIST = "4da1ec46a20b1ab2a80ecf98d3d61d19"  # suffix "pricelist"


class _FakeResponse:
    def __init__(self, payload, status_code=200, content=b"x", raise_value_error=False):
        self._payload = payload
        self.status_code = status_code
        self.content = content
        self._raise_value_error = raise_value_error

    def json(self):
        if self._raise_value_error:
            raise ValueError("not json")
        return self._payload


@tagged("post_install", "-at_install", "custom_ppob_biller_digiflazz")
class TestDigiflazzAdapter(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        Mapping = cls.env["custom.ppob.account.mapping"]
        cls.klass = cls.env["custom.ppob.product.class"].search([("code", "=", "TELKO")], limit=1)
        assert cls.klass, "TELKO class should be seeded by custom_ppob_core post_init"
        cls.bank = Mapping._get_account("cash_bca_escrow", cls.company)

        cls.env["ir.config_parameter"].sudo().set_param("digiflazz.test.apikey", _API_KEY)

        cls.mitra = cls.env["res.partner"].create(
            {
                "name": "Mitra Digiflazz Test",
                "x_custom_ppob_is_mitra": True,
                "x_custom_ppob_mitra_code": "MTRDF1",
            }
        )
        cls.vendor = cls.env["res.partner"].create(
            {
                "name": "Digiflazz Vendor",
                "x_custom_ppob_is_provider": True,
            }
        )
        cls.product = cls.env["custom.ppob.product"].create(
            {
                "code": "DFTSEL5",
                "name": "TSEL Pulsa 5k (digiflazz test)",
                "class_id": cls.klass.id,
                "denom": 5000.0,
                "cost_price_default": 4900.0,
            }
        )
        cls.postpaid_product = cls.env["custom.ppob.product"].create(
            {
                "code": "DFPLNPOST",
                "name": "PLN Postpaid (digiflazz test)",
                "class_id": cls.klass.id,
                "denom": 0.0,
                "cost_price_default": 100000.0,
                "inquiry_required": True,
            }
        )
        cls.wallet = cls.env["custom.ppob.wallet"].create(
            {
                "partner_id": cls.mitra.id,
                "class_id": cls.klass.id,
            }
        )
        cls.wallet._atomic_credit(
            amount=5_000_000.0,
            reason="seed",
            counterpart_account=cls.bank,
            move_type="topup",
        )
        cls.provider = cls.env["custom.ppob.provider"].create(
            {
                "code": "DIGIFLAZZ",
                "name": "Digiflazz",
                "partner_id": cls.vendor.id,
                "settlement_mode": "prepaid_deposit",
                "bucket_mode": "bulky",
                "adapter_class": "ppob_digiflazz",
                "digiflazz_username": _USERNAME,
                "credential_ref": "digiflazz.test.apikey",
            }
        )
        cls.provider.action_ensure_buckets()
        cls.provider.bucket_ids._atomic_credit(
            dpp_amount=5_000_000.0,
            tax_amount=0.0,
            gross_amount=5_000_000.0,
            reason="seed bucket",
            counterpart_account=cls.bank,
            move_type="topup",
        )
        cls.env["custom.ppob.provider.sku.map"].create(
            {
                "provider_id": cls.provider.id,
                "product_id": cls.product.id,
                "provider_sku": "tsel5",
                "buy_price": 4900.0,
            }
        )
        cls.env["custom.ppob.provider.sku.map"].create(
            {
                "provider_id": cls.provider.id,
                "product_id": cls.postpaid_product.id,
                "provider_sku": "pln",
                "buy_price": 100000.0,
            }
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_txn(self, product=None, key="DF-1"):
        return self.env["custom.ppob.transaction"].create(
            {
                "mitra_id": self.mitra.id,
                "product_id": (product or self.product).id,
                "msisdn": "087800001233",
                "sell_price": 5000.0,
                "cost_price": 4900.0,
                "idempotency_key": key,
            }
        )

    def _reply(self, payload, status_code=200):
        """Patch requests.post to answer with ``payload`` and capture what the
        adapter actually sent, so the request shape can be asserted.

        The fake ECHOES ref_id back the way Digiflazz documents, rather than
        answering with a ref of its own. That detail is load-bearing: the reaper
        finds a transaction by the provider_ref that pay() recorded, so a mock
        that invented its own ref would silently pass tests that the real
        (echoing) server would fail -- or, worse, hide that the lookup works
        only because the mock was generous.
        """
        captured = {}

        def _fake_post(url, data=None, headers=None, timeout=None):
            captured["url"] = url
            captured["body"] = json.loads(data.decode("utf-8"))
            captured["headers"] = headers
            captured["timeout"] = timeout
            answer = payload
            sent_ref = captured["body"].get("ref_id")
            if sent_ref and isinstance(answer.get("data"), dict) and "ref_id" in answer["data"]:
                answer = dict(answer, data=dict(answer["data"], ref_id=sent_ref))
            return _FakeResponse(answer, status_code=status_code)

        return patch(
            "odoo.addons.custom_ppob_biller_digiflazz.models.adapter_digiflazz.requests.post", side_effect=_fake_post
        ), captured

    def _dispatch_with_reply(self, payload, product=None, status_code=200, key="DF-1"):
        txn = self._make_txn(product=product, key=key)
        patcher, captured = self._reply(payload, status_code=status_code)
        with patcher:
            txn._dispatch_one()
        return txn, captured

    # ------------------------------------------------------------------
    # Signature & request shape (against the published spec)
    # ------------------------------------------------------------------

    def test_sign_is_md5_of_username_apikey_refid(self):
        txn, captured = self._dispatch_with_reply(
            {
                "data": {
                    "ref_id": "x",
                    "status": "Sukses",
                    "rc": "00",
                    "sn": "SN1",
                    "price": 4900,
                }
            }
        )
        ref_id = txn.digiflazz_ref_id
        # The adapter must reproduce Digiflazz's own md5 signature; the test asserts
        # byte-for-byte equality with it, so the algorithm is fixed by the vendor.
        # nosemgrep: python.lang.security.insecure-hash-algorithms-md5.insecure-hash-algorithm-md5,semgrep.weak-hash-md5-sha1
        # noqa: S324 alongside the nosemgrep above, because ruff does not read it.
        expected = hashlib.md5(f"{_USERNAME}{_API_KEY}{ref_id}".encode("utf-8")).hexdigest()  # noqa: S324
        self.assertEqual(captured["body"]["sign"], expected)

    def test_prepaid_topup_sends_no_commands_key(self):
        """Prepaid topup is the bare body; only postpaid carries `commands`."""
        _txn, captured = self._dispatch_with_reply(
            {
                "data": {
                    "ref_id": "x",
                    "status": "Sukses",
                    "rc": "00",
                    "price": 4900,
                }
            }
        )
        self.assertNotIn("commands", captured["body"])
        self.assertEqual(captured["body"]["buyer_sku_code"], "tsel5")
        self.assertEqual(captured["body"]["customer_no"], "087800001233")
        self.assertEqual(captured["body"]["username"], _USERNAME)

    def test_posts_to_transaction_endpoint(self):
        _txn, captured = self._dispatch_with_reply(
            {
                "data": {
                    "ref_id": "x",
                    "status": "Sukses",
                    "rc": "00",
                    "price": 4900,
                }
            }
        )
        self.assertEqual(captured["url"], "https://api.digiflazz.com/v1/transaction")

    def test_postpaid_pay_sends_pay_pasca(self):
        _txn, captured = self._dispatch_with_reply(
            {"data": {"ref_id": "x", "status": "Sukses", "rc": "00", "price": 100000}},
            product=self.postpaid_product,
            key="DF-POST",
        )
        self.assertEqual(captured["body"]["commands"], "pay-pasca")

    def test_testing_flag_forwarded_only_when_set(self):
        _txn, captured = self._dispatch_with_reply(
            {
                "data": {
                    "ref_id": "x",
                    "status": "Sukses",
                    "rc": "00",
                    "price": 4900,
                }
            }
        )
        self.assertNotIn("testing", captured["body"])
        self.provider.digiflazz_testing = True
        _txn2, captured2 = self._dispatch_with_reply(
            {
                "data": {
                    "ref_id": "x",
                    "status": "Sukses",
                    "rc": "00",
                    "price": 4900,
                }
            },
            key="DF-2",
        )
        self.assertTrue(captured2["body"]["testing"])

    def test_api_key_never_leaves_config_parameter(self):
        """The key must sign the request, never ride in the body."""
        _txn, captured = self._dispatch_with_reply(
            {
                "data": {
                    "ref_id": "x",
                    "status": "Sukses",
                    "rc": "00",
                    "price": 4900,
                }
            }
        )
        self.assertNotIn(_API_KEY, json.dumps(captured["body"]))

    # ------------------------------------------------------------------
    # ref_id: the idempotency contract
    # ------------------------------------------------------------------

    def test_ref_id_is_derived_from_name_without_slashes(self):
        txn, captured = self._dispatch_with_reply(
            {
                "data": {
                    "ref_id": "x",
                    "status": "Sukses",
                    "rc": "00",
                    "price": 4900,
                }
            }
        )
        self.assertTrue(txn.digiflazz_ref_id)
        self.assertNotIn("/", txn.digiflazz_ref_id)
        self.assertEqual(txn.digiflazz_ref_id, txn.name.replace("/", "-"))
        self.assertEqual(captured["body"]["ref_id"], txn.digiflazz_ref_id)

    def test_ref_id_is_stable_across_status_checks(self):
        """Regenerating a ref_id would turn a status check into a new sale."""
        txn, _ = self._dispatch_with_reply(
            {
                "data": {
                    "ref_id": "x",
                    "status": "Pending",
                    "rc": "03",
                    "price": 4900,
                }
            }
        )
        original = txn.digiflazz_ref_id
        txn.dispatched_at = fields.Datetime.now() - timedelta(minutes=5)
        adapter = self.provider._get_adapter()
        patcher, captured = self._reply(
            {
                "data": {
                    "ref_id": original,
                    "status": "Sukses",
                    "rc": "00",
                    "sn": "SN9",
                    "price": 4900,
                }
            }
        )
        with patcher:
            adapter.status(original)
        self.assertEqual(txn.digiflazz_ref_id, original)
        self.assertEqual(captured["body"]["ref_id"], original)

    def test_retry_clone_gets_a_fresh_ref_id(self):
        """A retry is a NEW sale. Reusing the ref_id would make Digiflazz replay
        the old outcome and deliver nothing."""
        txn, _ = self._dispatch_with_reply(
            {
                "data": {
                    "ref_id": "x",
                    "status": "Gagal",
                    "rc": "01",
                    "message": "gagal",
                    "price": 4900,
                }
            }
        )
        self.assertEqual(txn.state, "failed")
        action = txn.action_retry()
        clone = self.env["custom.ppob.transaction"].browse(action["res_id"])
        self.assertFalse(clone.digiflazz_ref_id)
        self.assertNotEqual(clone.name, txn.name)

    # ------------------------------------------------------------------
    # Tri-state mapping -- the part that moves money
    # ------------------------------------------------------------------

    def test_sukses_settles_and_keeps_the_debit(self):
        txn, _ = self._dispatch_with_reply(
            {
                "data": {
                    "ref_id": "x",
                    "status": "Sukses",
                    "rc": "00",
                    "sn": "TOKEN-1",
                    "price": 4900,
                }
            }
        )
        self.assertEqual(txn.state, "success")
        self.assertEqual(txn.serial_token, "TOKEN-1")
        self.assertTrue(txn.wallet_move_id)
        self.assertFalse(txn.wallet_refund_move_id)

    def test_gagal_refunds(self):
        txn, _ = self._dispatch_with_reply(
            {
                "data": {
                    "ref_id": "x",
                    "status": "Gagal",
                    "rc": "01",
                    "message": "produk gangguan",
                    "price": 4900,
                }
            }
        )
        self.assertEqual(txn.state, "failed")
        self.assertTrue(txn.wallet_refund_move_id, "a confirmed failure must refund")
        self.assertEqual(txn.error_message, "produk gangguan")

    def test_pending_does_not_refund(self):
        """THE critical case. Digiflazz answers Pending routinely; refunding
        there hands the mitra their money back on a sale that then completes."""
        txn, _ = self._dispatch_with_reply(
            {
                "data": {
                    "ref_id": "x",
                    "status": "Pending",
                    "rc": "03",
                    "message": "sedang diproses",
                    "price": 4900,
                }
            }
        )
        self.assertEqual(txn.state, "in_progress")
        self.assertFalse(txn.wallet_refund_move_id, "pending must NEVER refund")
        self.assertFalse(txn.completed_at)

    @mute_logger("odoo.addons.custom_ppob_biller_digiflazz.models.adapter_digiflazz")
    def test_unknown_status_is_treated_as_pending_not_failure(self):
        """An unrecognised status must not refund: we cannot tell a failure from
        a success we failed to parse."""
        txn, _ = self._dispatch_with_reply(
            {
                "data": {
                    "ref_id": "x",
                    "status": "Antrian",
                    "rc": "99",
                    "price": 4900,
                }
            }
        )
        self.assertEqual(txn.state, "in_progress")
        self.assertFalse(txn.wallet_refund_move_id)

    def test_http_error_refunds(self):
        txn, _ = self._dispatch_with_reply({"message": "bad gateway"}, status_code=502)
        self.assertEqual(txn.state, "failed")
        self.assertEqual(txn.error_code, "HTTP502")
        self.assertTrue(txn.wallet_refund_move_id)

    @mute_logger("odoo.addons.custom_ppob_biller_digiflazz.models.adapter_digiflazz")
    def test_transport_exception_refunds(self):
        import requests as _requests

        txn = self._make_txn(key="DF-EXC")
        with patch(
            "odoo.addons.custom_ppob_biller_digiflazz.models.adapter_digiflazz.requests.post",
            side_effect=_requests.RequestException("boom"),
        ):
            txn._dispatch_one()
        self.assertEqual(txn.state, "failed")
        self.assertTrue(txn.wallet_refund_move_id)

    # ------------------------------------------------------------------
    # status(): the guards that stand between a read and a duplicate sale
    # ------------------------------------------------------------------

    def test_status_refuses_within_min_age(self):
        """Digiflazz: repeat calls inside a minute can duplicate the process --
        and a prepaid status check IS a repeat call."""
        txn, _ = self._dispatch_with_reply(
            {
                "data": {
                    "ref_id": "x",
                    "status": "Pending",
                    "rc": "03",
                    "price": 4900,
                }
            }
        )
        adapter = self.provider._get_adapter()
        with patch("odoo.addons.custom_ppob_biller_digiflazz.models.adapter_digiflazz.requests.post") as mocked:
            result = adapter.status(txn.digiflazz_ref_id)
        mocked.assert_not_called()
        self.assertIsNone(result.ok, "too-soon must read as still-in-progress, not failure")
        self.assertEqual(result.error_code, "DIGIFLAZZ_STATUS_TOO_SOON")

    @mute_logger("odoo.addons.custom_ppob_biller_digiflazz.models.adapter_digiflazz")
    def test_status_refuses_beyond_max_age(self):
        """Past retention, a re-sent ref_id books a NEW charged sale."""
        txn, _ = self._dispatch_with_reply(
            {
                "data": {
                    "ref_id": "x",
                    "status": "Pending",
                    "rc": "03",
                    "price": 4900,
                }
            }
        )
        txn.dispatched_at = fields.Datetime.now() - timedelta(days=120)
        adapter = self.provider._get_adapter()
        with patch("odoo.addons.custom_ppob_biller_digiflazz.models.adapter_digiflazz.requests.post") as mocked:
            result = adapter.status(txn.digiflazz_ref_id)
        mocked.assert_not_called()
        self.assertIsNone(result.ok)
        self.assertEqual(result.error_code, "DIGIFLAZZ_STATUS_TOO_OLD")

    def test_status_proceeds_between_the_guards(self):
        txn, _ = self._dispatch_with_reply(
            {
                "data": {
                    "ref_id": "x",
                    "status": "Pending",
                    "rc": "03",
                    "price": 4900,
                }
            }
        )
        txn.dispatched_at = fields.Datetime.now() - timedelta(minutes=5)
        adapter = self.provider._get_adapter()
        patcher, captured = self._reply(
            {
                "data": {
                    "ref_id": txn.digiflazz_ref_id,
                    "status": "Sukses",
                    "rc": "00",
                    "sn": "SN-LATE",
                    "price": 4900,
                }
            }
        )
        with patcher:
            result = adapter.status(txn.digiflazz_ref_id)
        self.assertTrue(result.ok)
        self.assertEqual(result.raw["state"], "success", "raw.state must speak the engine's vocabulary for the reaper")
        self.assertEqual(captured["body"]["ref_id"], txn.digiflazz_ref_id)

    def test_status_of_unknown_ref_is_not_a_failure(self):
        adapter = self.provider._get_adapter()
        with patch("odoo.addons.custom_ppob_biller_digiflazz.models.adapter_digiflazz.requests.post") as mocked:
            result = adapter.status("NOPE-404")
        mocked.assert_not_called()
        self.assertIsNone(result.ok)
        self.assertEqual(result.error_code, "DIGIFLAZZ_TXN_NOT_FOUND")

    def test_postpaid_status_uses_status_pasca(self):
        txn, _ = self._dispatch_with_reply(
            {"data": {"ref_id": "x", "status": "Pending", "rc": "03", "price": 100000}},
            product=self.postpaid_product,
            key="DF-POST-2",
        )
        txn.dispatched_at = fields.Datetime.now() - timedelta(minutes=5)
        adapter = self.provider._get_adapter()
        patcher, captured = self._reply(
            {
                "data": {
                    "ref_id": txn.digiflazz_ref_id,
                    "status": "Sukses",
                    "rc": "00",
                    "price": 100000,
                }
            }
        )
        with patcher:
            adapter.status(txn.digiflazz_ref_id)
        self.assertEqual(captured["body"]["commands"], "status-pasca")

    # ------------------------------------------------------------------
    # Reaper integration: pending must survive the reaper
    # ------------------------------------------------------------------

    def test_reaper_leaves_a_still_pending_transaction_alone(self):
        """The reaper must not refund a transaction the provider says is still
        being processed -- otherwise every async sale is refunded the moment it
        goes stale, while the provider goes on to deliver it."""
        txn, _ = self._dispatch_with_reply(
            {
                "data": {
                    "ref_id": "x",
                    "status": "Pending",
                    "rc": "03",
                    "price": 4900,
                }
            }
        )
        self.assertEqual(txn.state, "in_progress")
        txn.dispatched_at = fields.Datetime.now() - timedelta(minutes=30)
        patcher, _captured = self._reply(
            {
                "data": {
                    "ref_id": txn.digiflazz_ref_id,
                    "status": "Pending",
                    "rc": "03",
                    "price": 4900,
                }
            }
        )
        with patcher:
            self.env["custom.ppob.transaction"]._cron_reap_stale_inprogress()
        self.assertEqual(txn.state, "in_progress", "still-pending must survive the reaper")
        self.assertFalse(txn.wallet_refund_move_id)

    def test_reaper_settles_a_transaction_that_completed_late(self):
        txn, _ = self._dispatch_with_reply(
            {
                "data": {
                    "ref_id": "x",
                    "status": "Pending",
                    "rc": "03",
                    "price": 4900,
                }
            }
        )
        txn.dispatched_at = fields.Datetime.now() - timedelta(minutes=30)
        patcher, _captured = self._reply(
            {
                "data": {
                    "ref_id": txn.digiflazz_ref_id,
                    "status": "Sukses",
                    "rc": "00",
                    "sn": "SN-LATE",
                    "price": 4900,
                }
            }
        )
        with patcher:
            self.env["custom.ppob.transaction"]._cron_reap_stale_inprogress()
        self.assertEqual(txn.state, "success")
        self.assertEqual(txn.serial_token, "SN-LATE")
        self.assertFalse(txn.wallet_refund_move_id)

    def test_reaper_refunds_a_transaction_that_failed_late(self):
        txn, _ = self._dispatch_with_reply(
            {
                "data": {
                    "ref_id": "x",
                    "status": "Pending",
                    "rc": "03",
                    "price": 4900,
                }
            }
        )
        txn.dispatched_at = fields.Datetime.now() - timedelta(minutes=30)
        patcher, _captured = self._reply(
            {
                "data": {
                    "ref_id": txn.digiflazz_ref_id,
                    "status": "Gagal",
                    "rc": "01",
                    "message": "timeout di operator",
                    "price": 4900,
                }
            }
        )
        with patcher:
            self.env["custom.ppob.transaction"]._cron_reap_stale_inprogress()
        self.assertEqual(txn.state, "timeout")
        self.assertTrue(txn.wallet_refund_move_id, "a confirmed late failure must refund")

    # ------------------------------------------------------------------
    # Read-only endpoints
    # ------------------------------------------------------------------

    def test_cek_saldo_signs_with_depo_suffix(self):
        adapter = self.provider._get_adapter()
        patcher, captured = self._reply({"data": {"deposit": 500000.0}})
        with patcher:
            result = adapter.check_balance()
        self.assertTrue(result.ok)
        self.assertEqual(result.amount, 500000.0)
        self.assertEqual(captured["url"], "https://api.digiflazz.com/v1/cek-saldo")
        self.assertEqual(captured["body"]["cmd"], "deposit")
        self.assertEqual(captured["body"]["sign"], _SIGN_DEPOSIT)

    def test_price_list_signs_with_pricelist_suffix(self):
        adapter = self.provider._get_adapter()
        patcher, captured = self._reply({"data": [{"buyer_sku_code": "tsel5", "price": 4900}]})
        with patcher:
            result = adapter.price_list()
        self.assertTrue(result.ok)
        self.assertEqual(captured["url"], "https://api.digiflazz.com/v1/price-list")
        self.assertEqual(captured["body"]["cmd"], "prepaid")
        self.assertEqual(captured["body"]["sign"], _SIGN_PRICELIST)

    def test_topup_is_refused_rather_than_faked(self):
        adapter = self.provider._get_adapter()
        with self.assertRaises(NotImplementedError):
            adapter.topup(100000.0)

    # ------------------------------------------------------------------
    # Misconfiguration
    # ------------------------------------------------------------------

    @mute_logger("odoo.addons.custom_ppob_sale.models.ppob_transaction")
    def test_missing_credentials_fails_loudly_and_refunds(self):
        self.provider.digiflazz_username = False
        txn = self._make_txn(key="DF-NOCRED")
        txn._dispatch_one()
        self.assertEqual(txn.state, "failed")
        self.assertEqual(txn.error_code, "ADAPTER_EXC")
        self.assertTrue(txn.wallet_refund_move_id)

    def test_status_min_age_below_one_second_rejected(self):
        from odoo.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            self.provider.digiflazz_status_min_age_s = 0
