# -*- coding: utf-8 -*-
"""Tests for chatbot script + step transitions."""

from odoo.tests.common import TransactionCase
from odoo.tests import tagged


@tagged("post_install", "-at_install", "custom_livechat")
class TestChatbotStep(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Script = cls.env["custom.livechat.chatbot.script"]
        cls.Step = cls.env["custom.livechat.chatbot.step"]
        cls.script = cls.Script.create(
            {
                "name": "Welcome Bot",
                "is_active": True,
            }
        )
        # Sequence: greet -> question -> end
        cls.step_greet = cls.Step.create(
            {
                "script_id": cls.script.id,
                "sequence": 10,
                "step_type": "text",
                "message": "Hi! Welcome.",
            }
        )
        cls.step_question = cls.Step.create(
            {
                "script_id": cls.script.id,
                "sequence": 20,
                "step_type": "question",
                "message": "Are you here for billing?",
                "expected_answers": "yes|y, billing",
            }
        )
        cls.step_end_no = cls.Step.create(
            {
                "script_id": cls.script.id,
                "sequence": 30,
                "step_type": "end",
                "message": "OK, have a great day!",
            }
        )
        # Default fallback for the question (used when no expected match)
        cls.step_fallback = cls.Step.create(
            {
                "script_id": cls.script.id,
                "sequence": 100,
                "step_type": "forward_to_operator",
                "message": "Let me hand you to a human.",
            }
        )
        cls.step_question.next_step_default = cls.step_fallback.id

    def test_first_step(self):
        first = self.script.get_first_step()
        self.assertEqual(first, self.step_greet)

    def test_text_step_walks_sequential(self):
        result = self.Step.get_next_step(self.step_greet.id, "")
        self.assertTrue(result["found"])
        self.assertEqual(result["step_id"], self.step_question.id)
        self.assertEqual(result["step_type"], "question")

    def test_question_step_match_goes_next(self):
        result = self.Step.get_next_step(self.step_question.id, "yes please")
        self.assertTrue(result["found"])
        self.assertEqual(result["step_id"], self.step_end_no.id)

    def test_question_step_no_match_uses_default(self):
        result = self.Step.get_next_step(self.step_question.id, "I don't know")
        self.assertTrue(result["found"])
        self.assertEqual(result["step_id"], self.step_fallback.id)
        self.assertEqual(result["step_type"], "forward_to_operator")

    def test_end_step_terminates(self):
        result = self.Step.get_next_step(self.step_end_no.id, "anything")
        self.assertFalse(result["found"])

    def test_forward_to_operator_terminates(self):
        result = self.Step.get_next_step(self.step_fallback.id, "anything")
        self.assertFalse(result["found"])

    def test_bad_regex_falls_back_to_substring(self):
        bad = self.Step.create(
            {
                "script_id": self.script.id,
                "sequence": 5,
                "step_type": "question",
                "message": "Pick a topic.",
                "expected_answers": "[invalid(regex, hello",
            }
        )
        self.assertTrue(bad._match_user_message("say hello world"))
        self.assertFalse(bad._match_user_message("goodbye"))

    def test_invalid_current_id(self):
        result = self.Step.get_next_step(0, "x")
        self.assertFalse(result["found"])

    def test_step_count_computed(self):
        # 4 steps created in setUpClass
        self.assertEqual(self.script.step_count, 4)
