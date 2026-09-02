# -*- coding: utf-8 -*-
"""Outbox: the idempotency guard that stands in for ESB's missing idempotency key."""

from __future__ import annotations

from odoo.tests import tagged

from .common import EsbTestCase, load_fixture


@tagged("post_install", "-at_install", "esb")
class TestEsbOutbox(EsbTestCase):
    def setUp(self):
        super().setUp()
        self.Outbox = self.env["custom.esb.outbox"]
        self.given_logged_in()
        self.set_flag("esb.push_enabled", "1")
        self.payload = {
            "itemJournalDate": "2026-07-21",
            "branchID": 373,
            "locationID": 964,
            "requestTemplateID": None,
            "itemJournalDetails": [{"ID": -1, "productDetailID": 2112, "purposeID": 9, "qty": -3, "hpp": 45}],
        }

    def _expect_no_existing(self):
        self.transport.register("GET", "/inventory/item-journal", load_fixture("item_journal_index_empty"))

    def _expect_create_ok(self):
        self.transport.register("POST", "/inventory/item-journal", load_fixture("item_journal_created"))

    # -- creation -----------------------------------------------------

    def test_idempotency_key_is_generated_and_stamped_into_additional_info(self):
        rec = self.Outbox.create({"doc_type": "item_journal", "payload": self.payload})

        self.assertTrue(rec.idempotency_key.startswith("ODOO-"))
        self.assertEqual(rec.payload["additionalInfo"], rec.idempotency_key)

    def test_existing_additional_info_is_preserved(self):
        payload = dict(self.payload, additionalInfo="Opname harian kitchen")
        rec = self.Outbox.create({"doc_type": "item_journal", "payload": payload})

        self.assertIn("Opname harian kitchen", rec.payload["additionalInfo"])
        self.assertIn(rec.idempotency_key, rec.payload["additionalInfo"])

    # -- happy path ---------------------------------------------------

    def test_push_creates_the_document_and_records_its_number(self):
        self._expect_no_existing()
        self._expect_create_ok()
        rec = self.Outbox.create({"doc_type": "item_journal", "payload": self.payload})

        rec.action_push_now()

        self.assertEqual(rec.state, "sent")
        self.assertEqual(rec.esb_doc_num, "IU202607210023")
        self.assertFalse(rec.adopted)
        self.assertEqual(self.transport.count("POST", "/inventory/item-journal"), 1)

    def test_payload_reaches_esb_verbatim(self):
        self._expect_no_existing()
        self._expect_create_ok()
        rec = self.Outbox.create({"doc_type": "item_journal", "payload": self.payload})

        rec.action_push_now()

        sent = self.transport.calls_to("POST", "/inventory/item-journal")[0]["body"]
        self.assertEqual(sent["branchID"], 373)
        self.assertEqual(sent["itemJournalDetails"][0]["qty"], -3, "the signed delta must survive intact")

    # -- the idempotency guard ---------------------------------------

    def test_retry_adopts_the_existing_document_instead_of_duplicating(self):
        """The scenario this guard exists for: the first POST landed in ESB but
        the worker died before committing, so Odoo retries."""
        rec = self.Outbox.create({"doc_type": "item_journal", "payload": self.payload})
        existing = load_fixture("item_journal_index_existing")
        existing["result"]["data"][0]["additionalInfo"] = rec.idempotency_key
        self.transport.register("GET", "/inventory/item-journal", existing)
        self._expect_create_ok()

        rec.action_push_now()

        self.assertEqual(rec.state, "sent")
        self.assertEqual(rec.esb_doc_num, "IU202607210023")
        self.assertTrue(rec.adopted)
        self.assertEqual(
            self.transport.count("POST", "/inventory/item-journal"), 0, "no second document may be created"
        )

    def test_a_different_documents_key_is_not_adopted(self):
        """ESB filters additionalInfo as a substring, so the match is re-verified."""
        rec = self.Outbox.create({"doc_type": "item_journal", "payload": self.payload})
        existing = load_fixture("item_journal_index_existing")
        existing["result"]["data"][0]["additionalInfo"] = "ODOO-someoneelseskey"
        self.transport.register("GET", "/inventory/item-journal", existing)
        self._expect_create_ok()

        rec.action_push_now()

        self.assertFalse(rec.adopted)
        self.assertEqual(self.transport.count("POST", "/inventory/item-journal"), 1)

    def test_failed_lookup_aborts_the_push_rather_than_risking_a_duplicate(self):
        """If we cannot tell whether the document exists, posting blind is how
        duplicates get created. Fail loudly instead."""
        self.transport.register("GET", "/inventory/item-journal", load_fixture("validation_error"))
        self.transport.register("POST", "/inventory/item-journal", load_fixture("item_journal_created"))
        rec = self.Outbox.create({"doc_type": "item_journal", "payload": self.payload})

        rec.action_push_now()

        self.assertEqual(self.transport.count("POST", "/inventory/item-journal"), 0)
        self.assertIn("Idempotency lookup failed", rec.last_error)

    def test_double_push_of_the_same_row_does_not_repost(self):
        self._expect_no_existing()
        self._expect_create_ok()
        rec = self.Outbox.create({"doc_type": "item_journal", "payload": self.payload})

        rec.action_push_now()
        rec.action_push_now()

        self.assertEqual(self.transport.count("POST", "/inventory/item-journal"), 1)

    # -- authorization ------------------------------------------------

    def test_authorize_runs_only_when_the_flag_is_on(self):
        self._expect_no_existing()
        self._expect_create_ok()
        self.transport.register("PATCH", "/authorize", load_fixture("item_journal_authorized"))
        rec = self.Outbox.create({"doc_type": "item_journal", "payload": self.payload})

        rec.action_push_now()
        self.assertEqual(self.transport.count("PATCH", "/authorize"), 0, "off by default")

        self.set_flag("esb.auto_authorize_item_journal", "1")
        rec2 = self.Outbox.create({"doc_type": "item_journal", "payload": self.payload})
        rec2.action_push_now()

        self.assertEqual(self.transport.count("PATCH", "/authorize"), 1)
        self.assertEqual(rec2.state, "confirmed")

    def test_failed_authorize_leaves_the_document_sent_not_failed(self):
        """The journal exists and is valid; it just is not approved yet."""
        self.set_flag("esb.auto_authorize_item_journal", "1")
        self._expect_no_existing()
        self._expect_create_ok()
        self.transport.register("PATCH", "/authorize", load_fixture("validation_error"))
        rec = self.Outbox.create({"doc_type": "item_journal", "payload": self.payload})

        rec.action_push_now()

        self.assertEqual(rec.state, "sent")
        self.assertEqual(rec.esb_doc_num, "IU202607210023")
        self.assertIn("authorize failed", rec.last_error)

    # -- kill switches and failures -----------------------------------

    def test_push_disabled_blocks_every_outbound_write(self):
        self.set_flag("esb.push_enabled", "0")
        rec = self.Outbox.create({"doc_type": "item_journal", "payload": self.payload})

        rec.action_push_now()

        self.assertEqual(rec.state, "queued")
        self.assertEqual(self.transport.count("POST", "/inventory/item-journal"), 0)
        self.assertEqual(self.transport.count("GET", "/inventory/item-journal"), 0)

    def test_rejected_document_is_retried_then_marked_failed(self):
        self._expect_no_existing()
        self.transport.register("POST", "/inventory/item-journal", load_fixture("validation_error"))
        rec = self.Outbox.create({"doc_type": "item_journal", "payload": self.payload})

        for _attempt in range(5):
            rec.action_push_now()

        self.assertEqual(rec.state, "failed")
        self.assertIn("EC03100400", rec.last_error)

    def test_status_refresh_marks_authorized_documents_confirmed(self):
        self._expect_no_existing()
        self._expect_create_ok()
        rec = self.Outbox.create({"doc_type": "item_journal", "payload": self.payload})
        rec.action_push_now()
        viewed = load_fixture("item_journal_index_existing")
        viewed["result"] = dict(viewed["result"]["data"][0], statusID=3, statusName="Authorized")
        self.transport.routes.clear()
        self.transport.register("GET", "/inventory/item-journal/IU202607210023", viewed)

        self.Outbox._cron_refresh_status()

        self.assertEqual(rec.state, "confirmed")
        self.assertEqual(rec.esb_status_id, 3)

    def test_status_refresh_marks_rejected_documents_failed(self):
        self._expect_no_existing()
        self._expect_create_ok()
        rec = self.Outbox.create({"doc_type": "item_journal", "payload": self.payload})
        rec.action_push_now()
        viewed = load_fixture("item_journal_index_existing")
        viewed["result"] = dict(viewed["result"]["data"][0], statusID=2, statusName="Rejected")
        self.transport.routes.clear()
        self.transport.register("GET", "/inventory/item-journal/IU202607210023", viewed)

        self.Outbox._cron_refresh_status()

        self.assertEqual(rec.state, "failed")
        self.assertIn("Rejected", rec.last_error)

    def test_enqueue_rejects_unknown_document_types(self):
        from odoo.exceptions import UserError

        with self.assertRaises(UserError):
            self.Outbox.enqueue("not_a_real_doc_type", self.payload)
