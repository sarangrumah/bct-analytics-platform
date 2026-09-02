# -*- coding: utf-8 -*-
"""Automation builder: rules materialise base.automation + ir.actions.server."""

from __future__ import annotations

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestAutomationRule(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Rule = cls.env["studio.automation.rule"]
        cls.IrModel = cls.env["ir.model"]
        cls.partner_model = cls.IrModel.search([("model", "=", "res.partner")], limit=1)

    def test_simple_on_create_rule_creates_base_automation(self):
        rule = self.Rule.create(
            {
                "name": "Post note on partner create",
                "model_id": self.partner_model.id,
                "trigger": "on_create",
                "action_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Greet",
                            "template": "post_note",
                            "body": "New partner created!",
                        },
                    ),
                ],
            }
        )
        rule.action_apply()
        rule.invalidate_recordset()
        self.assertEqual(rule.state, "applied", rule.last_error)
        self.assertTrue(rule.base_automation_id)
        self.assertEqual(rule.base_automation_id.trigger, "on_create")
        self.assertEqual(rule.base_automation_id.model_id, self.partner_model)
        # Should have one server action attached.
        server_actions = rule.base_automation_id.action_server_ids
        self.assertEqual(len(server_actions), 1)
        self.assertIn("message_post", server_actions.code or "")

    def test_action_chain_emits_multiple_server_actions(self):
        rule = self.Rule.create(
            {
                "name": "Two-step chain",
                "model_id": self.partner_model.id,
                "trigger": "on_create",
                "action_ids": [
                    (0, 0, {"name": "Note", "template": "post_note", "body": "step 1"}),
                    (
                        0,
                        0,
                        {
                            "name": "Set",
                            "template": "set_field",
                            "target_field_id": self.env["ir.model.fields"]
                            .search(
                                [("model", "=", "res.partner"), ("name", "=", "comment")],
                                limit=1,
                            )
                            .id,
                            "target_value": "auto",
                        },
                    ),
                ],
            }
        )
        rule.action_apply()
        rule.invalidate_recordset()
        self.assertEqual(rule.state, "applied", rule.last_error)
        self.assertEqual(len(rule.base_automation_id.action_server_ids), 2)

    def test_branch_then_else_renders_conditional_code(self):
        rule = self.Rule.create(
            {
                "name": "Conditional branch",
                "model_id": self.partner_model.id,
                "trigger": "on_create",
            }
        )
        parent = self.env["studio.automation.action"].create(
            {
                "rule_id": rule.id,
                "name": "Parent",
                "template": "post_note",
                "condition": "record.is_company",
                "body": "parent",
            }
        )
        self.env["studio.automation.action"].create(
            {
                "rule_id": rule.id,
                "parent_action_id": parent.id,
                "branch_type": "then",
                "template": "post_note",
                "body": "then branch",
            }
        )
        self.env["studio.automation.action"].create(
            {
                "rule_id": rule.id,
                "parent_action_id": parent.id,
                "branch_type": "else",
                "template": "post_note",
                "body": "else branch",
            }
        )
        rule.action_apply()
        rule.invalidate_recordset()
        self.assertEqual(rule.state, "applied", rule.last_error)
        # The first server action should embed both branches.
        code = rule.base_automation_id.action_server_ids[0].code or ""
        self.assertIn("if (record.is_company)", code)
        self.assertIn("then branch", code)
        self.assertIn("else:", code)
        self.assertIn("else branch", code)

    def test_revert_deactivates_base_automation(self):
        rule = self.Rule.create(
            {
                "name": "Revertable",
                "model_id": self.partner_model.id,
                "trigger": "on_create",
                "action_ids": [
                    (0, 0, {"template": "post_note", "body": "hi", "name": "Note"}),
                ],
            }
        )
        rule.action_apply()
        rule.action_revert()
        self.assertEqual(rule.state, "draft")
        self.assertFalse(rule.base_automation_id.active)
