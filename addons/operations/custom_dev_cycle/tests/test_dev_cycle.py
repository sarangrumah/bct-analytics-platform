# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_dev_cycle")
class TestDevCycle(TransactionCase):
    def setUp(self):
        super().setUp()
        # Need a BRD document to satisfy required FK on brd.recommendation
        self.doc = self.env["brd.document"].create({"name": "Test BRD"})
        self.rec = self.env["brd.recommendation"].create(
            {
                "document_id": self.doc.id,
                "name": "custom_foo_bar",
                "scope": "Test scope",
            }
        )

    def test_create_cycle_branch_suggestion(self):
        cycle = self.env["dev.cycle"].create({"brd_recommendation_id": self.rec.id, "name": "Cycle A"})
        self.assertTrue(cycle.branch_name.startswith("feature/brd-"))
        self.assertIn("custom-foo-bar", cycle.branch_name)
        self.assertEqual(cycle.state, "backlog")

    def test_state_machine_forward(self):
        cycle = self.env["dev.cycle"].create({"brd_recommendation_id": self.rec.id, "name": "Cycle B"})
        cycle.action_start()
        self.assertEqual(cycle.state, "in_dev")
        self.assertTrue(cycle.started_at)
        cycle.action_to_review()
        cycle.action_to_qa()
        cycle.action_to_uat()
        cycle.action_deploy()
        self.assertEqual(cycle.state, "deployed")
        cycle.action_done()
        self.assertEqual(cycle.state, "done")
        self.assertTrue(cycle.completed_at)

    def test_state_machine_rejects_big_jump_back(self):
        cycle = self.env["dev.cycle"].create({"brd_recommendation_id": self.rec.id, "name": "Cycle C"})
        cycle.action_start()
        cycle.action_to_review()
        cycle.action_to_qa()
        with self.assertRaises(UserError):
            cycle.action_transition_state("backlog")

    def test_brd_recommendation_smart_button(self):
        action = self.rec.action_create_dev_cycle()
        self.assertEqual(action["res_model"], "dev.cycle")
        self.assertEqual(self.rec.dev_cycle_count, 1)

    def test_unknown_state_raises(self):
        cycle = self.env["dev.cycle"].create({"brd_recommendation_id": self.rec.id, "name": "Cycle D"})
        with self.assertRaises(UserError):
            cycle.action_transition_state("nonsense")
