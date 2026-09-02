# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_onboarding_journey")
class TestJourneyStateMachine(TransactionCase):
    def setUp(self):
        super().setUp()
        # Skip project sync during these focused state-machine tests.
        self.Journey = self.env["onboarding.journey"].with_context(_skip_journey_sync=True)
        self.Transition = self.env["onboarding.stage.transition"]

    def test_create_logs_initial_transition(self):
        j = self.Journey.create({"name": "T1"})
        self.assertEqual(j.stage, "draft")
        rows = self.Transition.search([("journey_id", "=", j.id)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.to_stage, "draft")
        self.assertFalse(rows.from_stage)

    def test_valid_transition(self):
        j = self.Journey.create({"name": "T2"})
        j.write({"stage": "intake"})
        self.assertEqual(j.stage, "intake")
        rows = self.Transition.search(
            [("journey_id", "=", j.id)],
            order="id asc",
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].to_stage, "intake")

    def test_invalid_transition_raises(self):
        j = self.Journey.create({"name": "T3"})
        with self.assertRaises(ValidationError):
            j.write({"stage": "tenant_live"})

    def test_force_transition_bypasses_guard(self):
        j = self.Journey.create({"name": "T4"})
        j.with_context(_force_stage=True).write({"stage": "tenant_live"})
        self.assertEqual(j.stage, "tenant_live")

    def test_sync_version_bumps_on_stage_write(self):
        j = self.Journey.create({"name": "T5"})
        v0 = j.sync_version
        j.write({"stage": "intake"})
        self.assertGreater(j.sync_version, v0)

    def test_public_token_unique_and_set(self):
        a = self.Journey.create({"name": "A"})
        b = self.Journey.create({"name": "B"})
        self.assertTrue(a.public_status_token)
        self.assertTrue(b.public_status_token)
        self.assertNotEqual(a.public_status_token, b.public_status_token)

    def test_transition_log_is_append_only(self):
        j = self.Journey.create({"name": "T6"})
        row = self.Transition.search([("journey_id", "=", j.id)], limit=1)
        from odoo.exceptions import AccessError

        with self.assertRaises(AccessError):
            row.write({"notes": "tampered"})
