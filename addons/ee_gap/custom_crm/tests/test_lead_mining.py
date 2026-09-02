# -*- coding: utf-8 -*-
"""Happy-path tests for custom.crm.lead.mining.request."""

from __future__ import annotations

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLeadMining(TransactionCase):
    def test_create_then_generate_leads(self):
        req = self.env["custom.crm.lead.mining.request"].create(
            {
                "industry": "Logistics",
                "country_id": self.env.ref("base.id").id,
                "employees_range": "11_50",
                "lead_number": 3,
            }
        )
        self.assertEqual(req.state, "draft")
        self.assertEqual(req.credits_used, 0)
        self.assertTrue(req.name and req.name != "New")

        action = req.action_get_leads()

        self.assertEqual(req.state, "done")
        self.assertEqual(len(req.generated_lead_ids), 3)
        self.assertEqual(req.credits_used, 3)
        self.assertEqual(action["res_model"], "crm.lead")
        for lead in req.generated_lead_ids:
            self.assertTrue(lead.name.startswith("[Mining]"))
            self.assertEqual(lead.x_lead_mining_request_id, req)

    def test_estimate_returns_notification(self):
        req = self.env["custom.crm.lead.mining.request"].create(
            {
                "lead_number": 2,
            }
        )
        action = req.action_get_lead_count()
        self.assertEqual(action["type"], "ir.actions.client")
        self.assertEqual(action["tag"], "display_notification")

    def test_done_request_cannot_regenerate(self):
        req = self.env["custom.crm.lead.mining.request"].create({"lead_number": 1})
        req.action_get_leads()
        with self.assertRaises(UserError):
            req.action_get_leads()
