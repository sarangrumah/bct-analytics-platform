# -*- coding: utf-8 -*-
import hashlib
import hmac
import json

from odoo.tests.common import HttpCase, tagged


@tagged("post_install", "-at_install", "custom_dev_cycle")
class TestDevCycleWebhook(HttpCase):
    def setUp(self):
        super().setUp()
        self.secret = "dummy-webhook-secret"
        self.env["ir.config_parameter"].sudo().set_param("dev_cycle.github_webhook_secret", self.secret)
        doc = self.env["brd.document"].sudo().create({"name": "BRD WH"})
        self.rec = self.env["brd.recommendation"].sudo().create({"document_id": doc.id, "name": "custom_wh_target"})
        self.cycle = (
            self.env["dev.cycle"]
            .sudo()
            .create(
                {
                    "name": "Cycle WH",
                    "brd_recommendation_id": self.rec.id,
                    "branch_name": "feature/brd-wh-test",
                    "state": "in_dev",
                }
            )
        )

    def _sign(self, body_bytes):
        return "sha256=" + hmac.new(self.secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()

    def test_github_pr_merged_advances_cycle(self):
        payload = {
            "action": "closed",
            "pull_request": {
                "number": 42,
                "html_url": "https://github.com/example/repo/pull/42",
                "state": "closed",
                "merged": True,
                "draft": False,
                "merged_at": "2026-01-01T12:00:00Z",
                "merged_by": {"login": "alice"},
                "requested_reviewers": [{"login": "bob"}],
                "head": {"ref": "feature/brd-wh-test"},
            },
        }
        body = json.dumps(payload).encode("utf-8")
        sig = self._sign(body)
        resp = self.url_open(
            "/devcycle/webhook/github",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "pull_request",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "ok")

        pr = self.env["dev.cycle.pr"].sudo().search([("cycle_id", "=", self.cycle.id)], limit=1)
        self.assertTrue(pr)
        self.assertEqual(pr.state, "merged")
        self.assertEqual(pr.pr_number, 42)

        # Merged but CI still pending → cycle should NOT have jumped to deployed yet.
        self.cycle.invalidate_recordset()
        self.assertNotEqual(self.cycle.state, "deployed")

        # Now send a check_run success.
        check_payload = {
            "check_run": {
                "status": "completed",
                "conclusion": "success",
                "pull_requests": [
                    {
                        "html_url": "https://github.com/example/repo/pull/42",
                        "url": "https://github.com/example/repo/pull/42",
                    }
                ],
            }
        }
        body2 = json.dumps(check_payload).encode("utf-8")
        sig2 = self._sign(body2)
        resp2 = self.url_open(
            "/devcycle/webhook/github",
            data=body2,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig2,
                "X-GitHub-Event": "check_run",
            },
        )
        self.assertEqual(resp2.status_code, 200)
        pr.invalidate_recordset()
        self.cycle.invalidate_recordset()
        self.assertEqual(pr.ci_status, "success")
        self.assertEqual(self.cycle.state, "deployed")

    def test_github_bad_signature_rejected(self):
        body = json.dumps({"foo": "bar"}).encode("utf-8")
        resp = self.url_open(
            "/devcycle/webhook/github",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=deadbeef",
                "X-GitHub-Event": "pull_request",
            },
        )
        self.assertEqual(resp.status_code, 401)
