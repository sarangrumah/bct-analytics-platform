# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_repairs")
class TestRepairRework(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Repair = self.env["repair.order"]

    def test_set_rework_marks_and_posts(self):
        repair = self.Repair.create({})
        self.assertFalse(repair.x_returned)
        msg_before = len(repair.message_ids)
        repair.action_set_rework()
        self.assertTrue(repair.x_returned)
        self.assertTrue(repair.x_return_date)
        self.assertGreater(len(repair.message_ids), msg_before, "a chatter message is posted")
