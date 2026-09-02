# -*- coding: utf-8 -*-
"""Stock opname: seeding from ESB, and the item journal a closed session emits."""

from __future__ import annotations

from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import FnbTestCase


@tagged("post_install", "-at_install", "esb", "fnb")
class TestOpname(FnbTestCase):
    # -- seeding ------------------------------------------------------

    def test_lines_are_seeded_from_the_esb_snapshot(self):
        self.given_snapshot(self.kitchen, self.ayam, 7500)
        self.given_snapshot(self.kitchen, self.beras, 50)
        session = self.given_session()

        session.action_generate_lines_from_esb()

        self.assertEqual(len(session.line_ids), 2)
        ayam_line = session.line_ids.filtered(lambda l: l.product_id == self.ayam)
        self.assertEqual(ayam_line.expected_qty, 7500, "expected qty comes from ESB, not stock.quant")

    def test_seeding_creates_the_odoo_location_on_demand(self):
        self.given_snapshot(self.kitchen, self.ayam, 10)
        session = self.env["custom.cycle.count.session"].create(
            {"esb_branch_id": self.branch.id, "esb_location_id": self.kitchen.id}
        )
        self.assertFalse(self.kitchen.location_id)

        session.action_generate_lines_from_esb()

        self.assertTrue(self.kitchen.location_id, "an Odoo location is created to anchor the lines")
        self.assertEqual(session.line_ids.location_id, self.kitchen.location_id)

    def test_seeding_twice_does_not_duplicate_lines(self):
        self.given_snapshot(self.kitchen, self.ayam, 10)
        session = self.given_session()

        session.action_generate_lines_from_esb()
        session.action_generate_lines_from_esb()

        self.assertEqual(len(session.line_ids), 1)

    def test_seeding_only_covers_the_sessions_own_location(self):
        self.given_snapshot(self.kitchen, self.ayam, 10)
        self.given_snapshot(self.chiller, self.beras, 20)
        session = self.given_session(self.kitchen)

        session.action_generate_lines_from_esb()

        self.assertEqual(session.line_ids.product_id, self.ayam)

    def test_non_esb_session_cannot_seed_from_esb(self):
        session = self.env["custom.cycle.count.session"].create({"company_id": self.env.company.id})

        with self.assertRaises(UserError):
            session.action_generate_lines_from_esb()

    def test_stale_snapshot_is_flagged(self):
        stale = fields.Datetime.now() - timedelta(days=3)
        self.given_snapshot(self.kitchen, self.ayam, 10, as_of=stale)
        session = self.given_session()

        session.action_generate_lines_from_esb()

        self.assertTrue(session.esb_stale_snapshot, "a 3-day-old snapshot must warn before posting")

    # -- the item journal ---------------------------------------------

    def test_item_journal_carries_the_signed_delta_not_the_counted_qty(self):
        """The single most dangerous bug in this integration: sending the counted
        quantity would post the entire stock balance as an adjustment."""
        self.given_snapshot(self.kitchen, self.ayam, 10)
        session = self.given_session()
        session.action_generate_lines_from_esb()
        session.action_start()
        self.count_line(session, self.ayam, 7)
        session.action_review()
        self.expect_item_journal()

        session.action_close()

        details = session.esb_outbox_id.payload["itemJournalDetails"]
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0]["qty"], -3, "counted 7 against expected 10 must post -3, not 7")

    def test_positive_variance_uses_the_gain_purpose(self):
        self.given_snapshot(self.kitchen, self.ayam, 10)
        session = self.given_session()
        session.action_generate_lines_from_esb()
        session.action_start()
        self.count_line(session, self.ayam, 12)
        session.action_review()
        self.expect_item_journal()

        session.action_close()

        detail = session.esb_outbox_id.payload["itemJournalDetails"][0]
        self.assertEqual(detail["qty"], 2)
        self.assertEqual(detail["purposeID"], 10, "a stock gain routes to the gain purpose's account")

    def test_negative_variance_uses_the_loss_purpose(self):
        self.given_snapshot(self.kitchen, self.ayam, 10)
        session = self.given_session()
        session.action_generate_lines_from_esb()
        session.action_start()
        self.count_line(session, self.ayam, 4)
        session.action_review()
        self.expect_item_journal()

        session.action_close()

        self.assertEqual(session.esb_outbox_id.payload["itemJournalDetails"][0]["purposeID"], 9)

    def test_missing_default_purpose_is_a_clear_error(self):
        self.env["custom.esb.purpose"].search([]).write({"is_default_loss": False, "is_default_gain": False})
        self.given_snapshot(self.kitchen, self.ayam, 10)
        session = self.given_session()
        session.action_generate_lines_from_esb()
        session.action_start()
        self.count_line(session, self.ayam, 4)
        session.action_review()

        with self.assertRaises(UserError) as ctx:
            session.action_close()
        self.assertIn("purpose", str(ctx.exception).lower())

    def test_one_journal_covers_the_whole_session(self):
        self.given_snapshot(self.kitchen, self.ayam, 10)
        self.given_snapshot(self.kitchen, self.beras, 50)
        session = self.given_session()
        session.action_generate_lines_from_esb()
        session.action_start()
        self.count_line(session, self.ayam, 8)
        self.count_line(session, self.beras, 55)
        session.action_review()
        self.expect_item_journal()

        session.action_close()

        # The push itself is a queue_job, so drive it explicitly here.
        self.assertEqual(len(session.esb_outbox_id), 1, "one outbox document, not one per line")
        self.assertEqual(len(session.esb_outbox_id.payload["itemJournalDetails"]), 2)
        session.esb_outbox_id.action_push_now()
        self.assertEqual(self.transport.count("POST", "/inventory/item-journal"), 1)

    def test_zero_variance_lines_are_excluded(self):
        """A zero-qty adjustment line is noise in ESB's ledger."""
        self.given_snapshot(self.kitchen, self.ayam, 10)
        self.given_snapshot(self.kitchen, self.beras, 50)
        session = self.given_session()
        session.action_generate_lines_from_esb()
        session.action_start()
        self.count_line(session, self.ayam, 10)  # spot on
        self.count_line(session, self.beras, 48)
        session.action_review()
        self.expect_item_journal()

        session.action_close()

        details = session.esb_outbox_id.payload["itemJournalDetails"]
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0]["productDetailID"], self.beras.x_esb_product_detail_id)

    def test_session_with_no_variance_posts_nothing(self):
        self.given_snapshot(self.kitchen, self.ayam, 10)
        session = self.given_session()
        session.action_generate_lines_from_esb()
        session.action_start()
        self.count_line(session, self.ayam, 10)
        session.action_review()
        self.expect_item_journal()

        session.action_close()

        self.assertFalse(session.esb_outbox_id, "nothing to adjust, so no ESB document at all")
        self.assertEqual(self.transport.count("POST", "/inventory/item-journal"), 0)

    def test_skipped_lines_never_reach_esb(self):
        self.given_snapshot(self.kitchen, self.ayam, 10)
        self.given_snapshot(self.kitchen, self.beras, 50)
        session = self.given_session()
        session.action_generate_lines_from_esb()
        session.action_start()
        self.count_line(session, self.ayam, 3)
        session.line_ids.filtered(lambda l: l.product_id == self.beras).status = "skipped"
        session.action_review()
        self.expect_item_journal()

        session.action_close()

        self.assertEqual(len(session.esb_outbox_id.payload["itemJournalDetails"]), 1)

    def test_journal_targets_the_sessions_branch_and_location(self):
        self.given_snapshot(self.kitchen, self.ayam, 10)
        session = self.given_session()
        session.action_generate_lines_from_esb()
        session.action_start()
        self.count_line(session, self.ayam, 9)
        session.action_review()
        self.expect_item_journal()

        session.action_close()

        payload = session.esb_outbox_id.payload
        self.assertEqual(payload["branchID"], 373)
        self.assertEqual(payload["locationID"], 964)
        self.assertIn(session.name, payload["additionalInfo"])

    def test_hpp_comes_from_the_esb_snapshot_value(self):
        self.given_snapshot(self.kitchen, self.ayam, 10, unit_value=45.0)
        session = self.given_session()
        session.action_generate_lines_from_esb()
        session.action_start()
        self.count_line(session, self.ayam, 9)
        session.action_review()
        self.expect_item_journal()

        session.action_close()

        self.assertEqual(session.esb_outbox_id.payload["itemJournalDetails"][0]["hpp"], 45.0)

    def test_closing_twice_does_not_post_twice(self):
        self.given_snapshot(self.kitchen, self.ayam, 10)
        session = self.given_session()
        session.action_generate_lines_from_esb()
        session.action_start()
        self.count_line(session, self.ayam, 9)
        session.action_review()
        self.expect_item_journal()

        session.action_close()
        first_outbox = session.esb_outbox_id
        session.action_close()

        self.assertEqual(session.esb_outbox_id, first_outbox, "the second close must not raise a second document")
        session.esb_outbox_id.action_push_now()
        session.esb_outbox_id.action_push_now()
        self.assertEqual(self.transport.count("POST", "/inventory/item-journal"), 1)

    # -- no phantom Odoo stock moves ----------------------------------

    def test_esb_backed_approval_creates_no_odoo_stock_move(self):
        """Odoo does not own this stock; a stock.move here would be a phantom
        movement and, on a valued product, a phantom journal entry."""
        self.given_snapshot(self.kitchen, self.ayam, 10)
        session = self.given_session()
        session.action_generate_lines_from_esb()
        session.action_start()
        line = self.count_line(session, self.ayam, 7)

        adjustment = self.env["custom.cycle.count.adjustment"].search([("line_id", "=", line.id)])
        adjustment.action_post()

        self.assertTrue(adjustment.posted, "the audit record is still kept")
        self.assertFalse(adjustment.stock_move_id, "but no Odoo movement is created")

    def test_non_esb_session_still_posts_an_odoo_stock_move(self):
        """The ESB behaviour must not leak into ordinary warehouse counts."""
        location = self.env["stock.location"].search([("usage", "=", "internal")], limit=1)
        session = self.env["custom.cycle.count.session"].create({"company_id": self.env.company.id})
        line = self.env["custom.cycle.count.line"].create(
            {
                "session_id": session.id,
                "location_id": location.id,
                "product_id": self.ayam.id,
                "expected_qty": 10,
            }
        )
        line.action_count(7)
        line.action_approve()

        adjustment = self.env["custom.cycle.count.adjustment"].search([("line_id", "=", line.id)])
        adjustment.action_post()

        self.assertTrue(adjustment.stock_move_id, "a non-ESB count still moves Odoo stock")

    # -- refresh before posting ---------------------------------------

    def test_refresh_expected_rereads_the_authoritative_balance(self):
        self.given_snapshot(self.kitchen, self.ayam, 10)
        session = self.given_session()
        session.action_generate_lines_from_esb()
        session.action_start()
        session.line_ids.action_count(7)
        # ESB says the real balance moved to 12 since the snapshot was taken.
        self.transport.register(
            "GET",
            "/product/stock-location",
            {
                "status": "ok",
                "code": "EC03100000",
                "result": {
                    "productDetailID": 2112,
                    "productName": "Ayam Utuh",
                    "uomName": "GR",
                    "qty": 12,
                    "stockQty": 12,
                },
            },
        )

        session.action_refresh_expected_from_esb()

        self.assertEqual(session.line_ids.expected_qty, 12)
        self.assertEqual(session.line_ids.variance_qty, -5, "the variance follows the refreshed balance")

    def test_push_is_blocked_while_the_kill_switch_is_off(self):
        self.param.set_param("esb.push_enabled", "0")
        self.given_snapshot(self.kitchen, self.ayam, 10)
        session = self.given_session()
        session.action_generate_lines_from_esb()
        session.action_start()
        self.count_line(session, self.ayam, 7)
        session.action_review()
        self.expect_item_journal()

        session.action_close()
        session.esb_outbox_id.action_push_now()

        self.assertEqual(self.transport.count("POST", "/inventory/item-journal"), 0)
        self.assertEqual(session.esb_outbox_id.state, "queued", "it waits rather than being lost")
