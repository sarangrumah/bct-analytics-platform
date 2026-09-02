# -*- coding: utf-8 -*-
"""Adapter HTTP layer with mocked ``requests`` — OAuth, submit, retry, 401."""

from __future__ import annotations

from unittest import mock

from odoo.exceptions import UserError
from odoo.tests import tagged

from ..models import coretax_adapter_pajakku as adapter_mod
from .common import PajakkuCommon


@tagged("post_install", "-at_install")
class TestAdapterHttp(PajakkuCommon):
    def setUp(self):
        super().setUp()
        adapter_mod._CB_STATE.clear()
        adapter_mod._TOKEN_CACHE.clear()

    # ----- guard -----

    def test_submit_refused_when_disabled(self):
        self.config.pajakku_enabled = False
        with self.assertRaises(UserError):
            self.Adapter.submit_xml(b"<xml/>", config=self.config, transaction_type="efaktur_keluaran")

    def test_submit_refused_when_circuit_open(self):
        cid = self.config.company_id.id
        adapter_mod._CB_STATE[cid] = {
            "fail_streak": 999,
            "open_until": 9_999_999_999.0,
        }
        with self.assertRaises(UserError):
            self.Adapter.submit_xml(b"<xml/>", config=self.config, transaction_type="efaktur_keluaran")

    # ----- OAuth + happy path -----

    def test_submit_happy_path_returns_uuid(self):
        token_resp = self._make_response(200, json_body={"access_token": "TKN", "expires_in": 3600})
        submit_resp = self._make_response(200, json_body={"submission_uuid": "uuid-123", "message": "queued ok"})
        with (
            mock.patch.object(adapter_mod.requests, "post", return_value=token_resp),
            mock.patch.object(adapter_mod.requests, "request", return_value=submit_resp),
        ):
            result = self.Adapter.submit_xml(
                b"<Faktur/>",
                config=self.config,
                transaction_type="efaktur_keluaran",
            )
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["submission_uuid"], "uuid-123")
        tx = self.Tx.browse(result["transaction_id"])
        self.assertEqual(tx.state, "submitted")
        self.assertEqual(tx.external_uuid, "uuid-123")

    def test_submit_records_usage(self):
        token_resp = self._make_response(200, json_body={"access_token": "TKN", "expires_in": 3600})
        submit_resp = self._make_response(200, json_body={"submission_uuid": "uuid-456"})
        with (
            mock.patch.object(adapter_mod.requests, "post", return_value=token_resp),
            mock.patch.object(adapter_mod.requests, "request", return_value=submit_resp),
        ):
            self.Adapter.submit_xml(b"<x/>", config=self.config, transaction_type="efaktur_keluaran")
        usage = self.Usage._get_current(self.config.company_id)
        self.assertEqual(usage.faktur_submits, 1)
        # api_calls must have ticked at least once (the request itself)
        self.assertGreaterEqual(usage.api_calls, 1)

    # ----- error paths -----

    def test_submit_error_increments_failure_streak(self):
        token_resp = self._make_response(200, json_body={"access_token": "TKN", "expires_in": 3600})
        error_resp = self._make_response(500, json_body={}, content=b"server boom")
        with (
            mock.patch.object(adapter_mod.requests, "post", return_value=token_resp),
            mock.patch.object(adapter_mod.requests, "request", return_value=error_resp),
            mock.patch.object(adapter_mod.time, "sleep"),
        ):  # don't actually wait
            with self.assertRaises(UserError):
                self.Adapter.submit_xml(b"<x/>", config=self.config, transaction_type="efaktur_keluaran")
        cid = self.config.company_id.id
        self.assertEqual(adapter_mod._CB_STATE.get(cid, {}).get("fail_streak"), 1)
        usage = self.Usage._get_current(self.config.company_id)
        self.assertGreaterEqual(usage.errors, 1)

    def test_oauth_failure_raises(self):
        bad_token_resp = self._make_response(401, json_body={"error": "invalid_client"})
        with (
            mock.patch.object(adapter_mod.requests, "post", return_value=bad_token_resp),
            mock.patch.object(adapter_mod.time, "sleep"),
        ):
            with self.assertRaises(UserError):
                self.Adapter.submit_xml(b"<x/>", config=self.config, transaction_type="efaktur_keluaran")

    # ----- query_nsfp -----

    def test_query_nsfp_returns_nsfp_on_approved(self):
        token_resp = self._make_response(200, json_body={"access_token": "TKN", "expires_in": 3600})
        # First call: submit
        submit_resp = self._make_response(200, json_body={"submission_uuid": "uuid-X"})
        with (
            mock.patch.object(adapter_mod.requests, "post", return_value=token_resp),
            mock.patch.object(adapter_mod.requests, "request", return_value=submit_resp),
        ):
            r = self.Adapter.submit_xml(b"<x/>", config=self.config, transaction_type="efaktur_keluaran")
        # Second call: query
        query_resp = self._make_response(
            200,
            json_body={"status": "approved", "nsfp": "0000123456789012"},
        )
        with (
            mock.patch.object(adapter_mod.requests, "post", return_value=token_resp),
            mock.patch.object(adapter_mod.requests, "request", return_value=query_resp),
        ):
            nsfp = self.Adapter.query_nsfp("uuid-X", config=self.config)
        self.assertEqual(nsfp, "0000123456789012")
        tx = self.Tx.browse(r["transaction_id"])
        tx.invalidate_recordset()
        self.assertEqual(tx.state, "approved")
        self.assertEqual(tx.nsfp, "0000123456789012")

    def test_query_nsfp_returns_none_while_pending(self):
        token_resp = self._make_response(200, json_body={"access_token": "TKN", "expires_in": 3600})
        pending_resp = self._make_response(200, json_body={"status": "processing"})
        with (
            mock.patch.object(adapter_mod.requests, "post", return_value=token_resp),
            mock.patch.object(adapter_mod.requests, "request", return_value=pending_resp),
        ):
            nsfp = self.Adapter.query_nsfp("uuid-pending", config=self.config)
        self.assertIsNone(nsfp)
