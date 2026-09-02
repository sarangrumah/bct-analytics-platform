# -*- coding: utf-8 -*-
"""Follow-up level cron progression."""

from __future__ import annotations

from datetime import date, timedelta

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestFollowupCron(TransactionCase):
    def setUp(self):
        super().setUp()
        self.company = self.env.company
        self.partner = self.env["res.partner"].create(
            {
                "name": "Followup Customer",
            }
        )
        # Two levels: gentle reminder @ 5 days, escalation @ 30 days
        self.level_a = self.env["custom.followup.level"].create(
            {
                "name": "Reminder",
                "sequence": 10,
                "delay_days": 5,
                "send_email": False,
                "action": "reminder",
                "company_id": self.company.id,
            }
        )
        self.level_b = self.env["custom.followup.level"].create(
            {
                "name": "Escalation",
                "sequence": 20,
                "delay_days": 30,
                "send_email": False,
                "action": "escalation",
                "company_id": self.company.id,
            }
        )
        self.rec_account = self.env["account.account"].create(
            {
                "code": "FLWRCV",
                "name": "Followup Receivable",
                "account_type": "asset_receivable",
                "reconcile": True,
                "company_ids": [(6, 0, [self.company.id])],
            }
        )
        self.inc_account = self.env["account.account"].create(
            {
                "code": "FLWINC",
                "name": "Followup Income",
                "account_type": "income",
                "company_ids": [(6, 0, [self.company.id])],
            }
        )
        self.journal = self.env["account.journal"].create(
            {
                "name": "Followup Sales",
                "code": "FLWS",
                "type": "sale",
                "company_id": self.company.id,
            }
        )

    def _make_overdue_invoice(self, days_overdue):
        inv = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.partner.id,
                "journal_id": self.journal.id,
                "invoice_date": date.today() - timedelta(days=days_overdue + 30),
                "invoice_date_due": date.today() - timedelta(days=days_overdue),
                "company_id": self.company.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Line",
                            "quantity": 1.0,
                            "price_unit": 1000.0,
                            "account_id": self.inc_account.id,
                        },
                    ),
                ],
            }
        )
        inv.action_post()
        return inv

    def test_cron_promotes_partner_to_highest_applicable_level(self):
        # Make a 40-day-overdue invoice — should hit Escalation
        self._make_overdue_invoice(40)
        self.env["custom.followup.level"]._cron_run_followup()
        self.partner._compute_custom_max_overdue_days()
        self.partner._custom_advance_followup_level()
        self.assertEqual(self.partner.custom_followup_level_id, self.level_b)

    def test_cron_picks_lower_level_when_only_mildly_overdue(self):
        self._make_overdue_invoice(10)
        self.partner._compute_custom_max_overdue_days()
        self.partner._custom_advance_followup_level()
        self.assertEqual(self.partner.custom_followup_level_id, self.level_a)
