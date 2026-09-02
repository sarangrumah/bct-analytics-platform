# -*- coding: utf-8 -*-
"""Exercise A/B test winner-evaluation logic without actual SMTP."""

from odoo import fields
from odoo.tests.common import TransactionCase


class TestAbWinner(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Mailing = cls.env["mailing.mailing"]
        cls.parent = Mailing.create(
            {
                "name": "Parent Mailing",
                "subject": "Original",
                "body_arch": "<p>Hi</p>",
                "body_html": "<p>Hi</p>",
                "mailing_model_id": cls.env.ref("base.model_res_partner").id,
                "mailing_domain": "[]",
            }
        )
        cls.variant_a = Mailing.create(
            {
                "name": "Parent [A]",
                "subject": "Var A",
                "body_arch": "<p>A</p>",
                "body_html": "<p>A</p>",
                "mailing_model_id": cls.env.ref("base.model_res_partner").id,
                "mailing_domain": "[]",
            }
        )
        cls.variant_b = Mailing.create(
            {
                "name": "Parent [B]",
                "subject": "Var B",
                "body_arch": "<p>B</p>",
                "body_html": "<p>B</p>",
                "mailing_model_id": cls.env.ref("base.model_res_partner").id,
                "mailing_domain": "[]",
            }
        )
        cls.ab = cls.env["custom.email.ab.test"].create(
            {
                "name": "Test AB",
                "mailing_id": cls.parent.id,
                "variant_a_subject": "Var A",
                "variant_b_subject": "Var B",
                "winner_metric": "opens",
                "state": "running",
                "variant_a_mailing_id": cls.variant_a.id,
                "variant_b_mailing_id": cls.variant_b.id,
                "evaluate_after": fields.Datetime.subtract(
                    fields.Datetime.now(),
                    hours=1,
                ),
            }
        )

    def _mk_trace(self, mailing, status, email):
        return self.env["mailing.trace"].create(
            {
                "mass_mailing_id": mailing.id,
                "model": "res.partner",
                "res_id": self.env.ref("base.partner_admin").id,
                "email": email,
                "trace_status": status,
            }
        )

    def test_variant_b_wins_on_opens(self):
        # A: 1 open, B: 3 opens
        self._mk_trace(self.variant_a, "open", "a1@example.id")
        for i in range(3):
            self._mk_trace(self.variant_b, "open", "b%d@example.id" % i)
        self.ab._evaluate_one()
        self.assertEqual(self.ab.state, "concluded")
        self.assertEqual(self.ab.winner, "b")
        self.assertEqual(self.ab.variant_a_score, 1)
        self.assertEqual(self.ab.variant_b_score, 3)

    def test_tie_is_recorded(self):
        self._mk_trace(self.variant_a, "open", "a1@example.id")
        self._mk_trace(self.variant_b, "open", "b1@example.id")
        self.ab._evaluate_one()
        self.assertEqual(self.ab.winner, "tie")

    def test_cron_picks_only_due_running_tests(self):
        # Move evaluate_after into the future — should not be picked up.
        self.ab.evaluate_after = fields.Datetime.add(
            fields.Datetime.now(),
            hours=1,
        )
        self._mk_trace(self.variant_a, "open", "a@example.id")
        processed = self.env["custom.email.ab.test"].cron_evaluate_winner()
        self.assertEqual(processed, 0)
        self.assertEqual(self.ab.state, "running")

    def test_split_pct_constraint(self):
        from odoo.exceptions import UserError

        with self.assertRaises(UserError):
            self.ab.split_pct = 0
        with self.assertRaises(UserError):
            self.ab.split_pct = 100
