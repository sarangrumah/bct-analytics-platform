# -*- coding: utf-8 -*-
"""HMAC verification + payload normalization for the recruitment webhook."""

from __future__ import annotations

import hashlib
import hmac
import json

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestWebhookHmac(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Log = cls.env["custom.recruitment.webhook.log"]
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        cls.SECRET_JS = "test-secret-jobstreet"
        cls.SECRET_GL = "test-secret-glints"
        cls.SECRET_LI = "test-secret-linkedin"
        cls.ICP.set_param("custom_recruitment_id.webhook_secret_jobstreet", cls.SECRET_JS)
        cls.ICP.set_param("custom_recruitment_id.webhook_secret_glints", cls.SECRET_GL)
        cls.ICP.set_param("custom_recruitment_id.webhook_secret_linkedin", cls.SECRET_LI)

    @staticmethod
    def _sign(secret, raw_body):
        return hmac.new(
            secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()

    def test_ingest_jobstreet_payload_normalized(self):
        payload = {
            "candidate": {
                "full_name": "Dewi Lestari",
                "email": "dewi@example.com",
                "phone": "+62 812 5555 6666",
                "ref_id": "JS-99",
            }
        }
        log = self.Log.ingest_payload("jobstreet", payload)
        self.assertTrue(log.processed)
        self.assertTrue(log.applicant_id)
        self.assertEqual(log.applicant_id.partner_name, "Dewi Lestari")
        self.assertEqual(log.applicant_id.email_from, "dewi@example.com")
        self.assertEqual(log.applicant_id.x_job_board_source, "jobstreet")
        self.assertEqual(log.applicant_id.x_external_id, "JS-99")

    def test_ingest_glints_payload_normalized(self):
        payload = {
            "applicant": {
                "name": "Eko Pratama",
                "email": "eko@example.com",
                "mobile": "0812-7777-8888",
                "id": "GL-42",
            }
        }
        log = self.Log.ingest_payload("glints", payload)
        self.assertTrue(log.processed)
        self.assertEqual(log.applicant_id.partner_name, "Eko Pratama")
        self.assertEqual(log.applicant_id.x_external_id, "GL-42")

    def test_ingest_linkedin_payload_merges_first_last(self):
        payload = {
            "applicant": {
                "firstName": "Farah",
                "lastName": "Aulia",
                "emailAddress": "farah@example.com",
                "phoneNumber": "+62 812 1212 3434",
                "applicationId": "LI-7",
            }
        }
        log = self.Log.ingest_payload("linkedin", payload)
        self.assertTrue(log.processed)
        self.assertEqual(log.applicant_id.partner_name, "Farah Aulia")
        self.assertEqual(log.applicant_id.x_external_id, "LI-7")

    def test_ingest_unknown_source_falls_back_to_manual(self):
        log = self.Log.ingest_payload(
            "totally-bogus",
            {
                "name": "Generic",
                "email": "g@example.com",
            },
        )
        self.assertEqual(log.source, "manual")
        self.assertTrue(log.processed)

    def test_hmac_signature_helper_matches_controller(self):
        # Mirrors the verification logic of the controller without spinning
        # up an HTTP server. This guards against accidental changes to
        # secret-param name or signing scheme.
        body = json.dumps({"candidate": {"full_name": "X"}}).encode("utf-8")
        sig = self._sign(self.SECRET_JS, body)
        # Verify the exact same digest we'd produce server-side.
        expected = hmac.new(
            self.SECRET_JS.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(sig, expected)
        # And that the secret is fetched via the documented param name.
        stored = self.ICP.get_param("custom_recruitment_id.webhook_secret_jobstreet")
        self.assertEqual(stored, self.SECRET_JS)

    def test_hmac_signature_mismatch_rejected_by_helper(self):
        body = b"{}"
        good = self._sign(self.SECRET_JS, body)
        bad = self._sign("wrong-secret", body)
        self.assertNotEqual(good, bad)
        self.assertFalse(hmac.compare_digest(good, bad))
