# -*- coding: utf-8 -*-
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDashboardAskAI(TransactionCase):
    def setUp(self):
        super().setUp()
        self.dashboard = self.env["custom.dashboard"].create(
            {
                "name": "AI Dashboard",
                "description": "Test dashboard for AI",
            }
        )
        self.env["custom.dashboard.tile"].create(
            {
                "dashboard_id": self.dashboard.id,
                "name": "Users",
                "tile_type": "count",
                "model_name": "res.users",
                "domain": "[]",
            }
        )

    def test_ask_ai_empty_question(self):
        action = self.dashboard.action_ask_ai("")
        self.assertEqual(action.get("tag"), "display_notification")
        self.assertEqual(action["params"]["type"], "warning")

    def test_ask_ai_success(self):
        with patch.object(
            type(self.env["custom.ai"]),
            "_recommend",
            return_value={"response": "Hello from AI"},
        ):
            result = self.dashboard.action_ask_ai("How many users?")
        self.assertTrue(result)
        self.assertEqual(self.dashboard.last_ai_question, "How many users?")
        self.assertIn("Hello from AI", self.dashboard.last_ai_answer)
        self.assertTrue(self.dashboard.last_ai_at)

    def test_ask_ai_failure_returns_notification(self):
        with patch.object(
            type(self.env["custom.ai"]),
            "_recommend",
            side_effect=RuntimeError("gateway down"),
        ):
            action = self.dashboard.action_ask_ai("anything")
        self.assertEqual(action.get("tag"), "display_notification")
        self.assertEqual(action["params"]["type"], "warning")

    def test_ai_payload_includes_tiles(self):
        payload = self.dashboard._custom_ai_payload("question?")
        self.assertEqual(payload["question"], "question?")
        self.assertEqual(payload["dashboard"], "AI Dashboard")
        self.assertEqual(payload["tile_count"], 1)
        self.assertEqual(payload["tiles"][0]["model_name"], "res.users")
