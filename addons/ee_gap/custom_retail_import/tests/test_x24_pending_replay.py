# -*- coding: utf-8 -*-
"""Tests for the pending-SKU registry and the X24DN replay path.

The problem being solved: X24DN parks a *whole transaction* when any of its lines
quotes a SKU the product master does not have, because posting a partial order would
leave it unbalanced against the X70D tender. Before this change the only trace was an
error string on a source row, so finishing the sale required a human to notice.

The registry closes that loop, and the risk it introduces is duplicate revenue — a
replay that posts a transaction twice, or one that trips over the guard meant to stop
a whole file being imported twice. Those two properties are what this suite pins:

* the guard in ``_post_x24`` still fires for a genuine second import, and does *not*
  fire for a replay of rows that were already read from the same file;
* a replay merges into the log's counters instead of overwriting them, and the log
  returns to ``imported`` only when the last parked row clears.

The heavy end of the path (creating ``pos.order`` records) needs a configured POS —
payment methods, sessions, tenders — which is environment, not logic. Those calls are
patched out here and the full posting is covered by the end-to-end verification on a
database clone instead. What remains under test is exactly the code this change added.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

_SALES_ROW = {
    "store_code": "S001",
    "trans_date": "2026-07-01",
    "register": "1",
    "transnum": "0001",
    "item_code": "ZT9YB00010",
    "waist": "34",
    "inseam": "10",
    "ean": "9991231300001",
    "item_description": "501 ORIGINAL",
    "retail_price": 749900,
    "net_qty": 1,
    "total_amount": 749900,
}


@tagged("post_install", "-at_install")
class TestPendingSkuRegistry(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Executor = cls.env["retail.import.executor"]
        cls.Pending = cls.env["retail.mdm.pending.sku"]
        cls.x101_profile = cls.env.ref("custom_retail_import.profile_retail_x101")
        cls.namespace = cls.x101_profile.namespace

    def _log(self, profile=None):
        return self.env["retail.import.log"].create(
            {"profile_id": (profile or self.x101_profile).id, "filename": "t.xlsx"}
        )

    def _parked_line(self, log, row=_SALES_ROW, row_number=3):
        return self.env["retail.import.line"].create(
            {
                "log_id": log.id,
                "row_number": row_number,
                "raw_data_json": json.dumps(row),
                "state": "error",
                "error_message": "not in X101 master",
            }
        )

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    def test_record_creates_entry_and_links_the_parked_row(self):
        log = self._log()
        line = self._parked_line(log)
        rec = self.Pending._record(_SALES_ROW, line=line)

        self.assertEqual(rec.composite_code, "ZT9YB000103410", "code+waist+inseam, as X101 stores it")
        self.assertEqual(rec.ean, "9991231300001")
        self.assertEqual(rec.state, "pending")
        self.assertEqual(rec.occurrence_count, 1)
        self.assertEqual(line.pending_sku_id, rec)
        self.assertEqual(rec.parked_line_count, 1)

    def test_second_sighting_increments_rather_than_duplicates(self):
        first = self.Pending._record(_SALES_ROW)
        second = self.Pending._record(dict(_SALES_ROW, transnum="0002"))
        self.assertEqual(first, second)
        self.assertEqual(first.occurrence_count, 2)

    def test_row_without_any_identifier_is_ignored(self):
        self.assertFalse(self.Pending._record({"store_code": "S001"}))

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------
    def test_master_arriving_registers_the_sku_and_enqueues_a_replay(self):
        log = self._log()
        line = self._parked_line(log)
        rec = self.Pending._record(_SALES_ROW, line=line)

        product = self.env["product.product"].create({"name": "501 ORIGINAL 34/10", "default_code": "ZT9YB000103410"})

        # queue_job defers with_delay() unless told otherwise; run it inline so the
        # assertion is about the replay actually being dispatched.
        with patch.object(type(self.Executor), "_job_replay_x24_parked", autospec=True, return_value=True) as replay:
            resolved = self.Pending.with_context(queue_job__no_delay=True)._resolve_and_replay(
                {"ZT9YB000103410"}, set()
            )

        self.assertEqual(resolved, rec)
        self.assertEqual(rec.state, "registered")
        self.assertEqual(rec.resolved_product_id, product)
        self.assertTrue(replay.called, "a replay job must be enqueued for the affected log")

    def test_resolution_by_gtin(self):
        rec = self.Pending._record(_SALES_ROW)
        self.env["product.product"].create({"name": "501", "default_code": "SOMETHINGELSE", "barcode": "9991231300001"})
        with patch.object(type(self.Executor), "_job_replay_x24_parked", autospec=True, return_value=True):
            resolved = self.Pending._resolve_and_replay(set(), {"9991231300001"})
        self.assertEqual(resolved, rec)
        self.assertEqual(rec.state, "registered")

    def test_unrelated_master_load_leaves_it_pending(self):
        rec = self.Pending._record(_SALES_ROW)
        with patch.object(type(self.Executor), "_job_replay_x24_parked", autospec=True, return_value=True):
            self.Pending._resolve_and_replay({"SOMETHING-ELSE"}, {"9999999999999"})
        self.assertEqual(rec.state, "pending")

    def test_code_known_but_product_absent_stays_pending(self):
        """The registry trusts a real lookup, not the caller's list of codes."""
        rec = self.Pending._record(_SALES_ROW)
        with patch.object(type(self.Executor), "_job_replay_x24_parked", autospec=True, return_value=True):
            self.Pending._resolve_and_replay({"ZT9YB000103410"}, set())
        self.assertEqual(rec.state, "pending")

    def test_x101_seam_triggers_resolution(self):
        """The whole point of the seam: the file import fires this too, not just the API."""
        self.Pending._record(_SALES_ROW)
        with patch.object(
            type(self.Pending), "_resolve_and_replay", autospec=True, return_value=self.Pending
        ) as resolve:
            self.Executor._x101_upsert_items(
                [
                    {
                        "product_code": "000YB-0001",
                        "description": "501 ORIGINAL",
                        "category": "MENS BOTTOMS",
                        "klass": "JEANS",
                        "subclass": "STRAIGHT",
                        "sku": "ZT9YB000103410",
                        "size": "34",
                        "inseam": "10",
                        "gtin": "9991231300001",
                        "retail_price": 749900,
                        "price_eff": None,
                        "_row": 3,
                    }
                ],
                self.namespace,
            )
        self.assertTrue(resolve.called)

    # ------------------------------------------------------------------
    # Log bookkeeping after a partial replay
    # ------------------------------------------------------------------
    def test_log_returns_to_imported_when_the_last_parked_row_clears(self):
        log = self._log()
        log.state = "partial"
        first = self._parked_line(log, row_number=3)
        second = self._parked_line(log, row_number=4)

        first.write({"state": "ok"})
        self.Executor._x24_refresh_log_errors(log)
        self.assertEqual(log.state, "partial", "one row is still parked")
        self.assertEqual(log.error_count, 1)

        second.write({"state": "ok"})
        self.Executor._x24_refresh_log_errors(log)
        self.assertEqual(log.state, "imported")
        self.assertEqual(log.error_count, 0)
        self.assertFalse(log.raw_payload)

    # ------------------------------------------------------------------
    # Auto-register holding category
    # ------------------------------------------------------------------
    def test_pending_category_is_created_once_and_pinned(self):
        first = self.Executor._mdm_pending_category()
        second = self.Executor._mdm_pending_category()
        self.assertEqual(first, second)
        self.assertEqual(
            self.env["ir.config_parameter"].sudo().get_param("retail_import.mdm_pending_categ_id"),
            str(first.id),
        )


@tagged("post_install", "-at_install")
class TestX24ReplayGuard(TransactionCase):
    """The whole-file guard must keep working, and must not block a replay."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Executor = cls.env["retail.import.executor"]
        cls.profile = cls.env["retail.import.profile"].search([("file_type", "=", "x24")], limit=1)
        if not cls.profile:
            cls.profile = cls.env["retail.import.profile"].create(
                {
                    "name": "X24 test",
                    "code": "x24_test",
                    "file_type": "x24",
                    "file_format": "xlsx",
                    "namespace": "retail",
                    "column_map": "{}",
                }
            )

    def _neutralise_pos(self):
        """Patch out everything that needs a configured POS, leaving our logic."""
        Executor = type(self.Executor)
        return [
            patch.object(Executor, "_x24_ensure_method_gl_split", autospec=True, return_value=None),
            patch.object(Executor, "_x24_automap_missing", autospec=True, return_value=None),
            patch.object(Executor, "_x24_resolve_tax", autospec=True, return_value=self.env["account.tax"]),
            patch.object(Executor, "_x24_tender_index", autospec=True, return_value={}),
            patch.object(Executor, "_ri_assert_stores_postable", autospec=True, return_value=None),
            patch.object(Executor, "_pos_close_and_backdate", autospec=True, return_value=None),
        ]

    def _run(self, log, replay):
        patches = self._neutralise_pos()
        for p in patches:
            p.start()
        try:
            return self.Executor._post_x24(self.profile, [], log, {}, replay=replay)
        finally:
            for p in patches:
                p.stop()

    def test_second_import_is_refused(self):
        self.env["retail.import.log"].create(
            {"profile_id": self.profile.id, "filename": "first.xlsx", "state": "imported"}
        )
        second = self.env["retail.import.log"].create({"profile_id": self.profile.id, "filename": "second.xlsx"})
        with self.assertRaises(UserError):
            self._run(second, replay=False)

    def test_replay_is_not_refused(self):
        """A replay re-enters the same log's own rows; the guard is about a second file."""
        log = self.env["retail.import.log"].create(
            {"profile_id": self.profile.id, "filename": "first.xlsx", "state": "imported"}
        )
        self.env["retail.import.log"].create(
            {"profile_id": self.profile.id, "filename": "other.xlsx", "state": "imported"}
        )
        result = self._run(log, replay=True)
        self.assertEqual(result["created"], 0)

    def test_replay_merges_counters_instead_of_resetting_them(self):
        log = self.env["retail.import.log"].create(
            {
                "profile_id": self.profile.id,
                "filename": "first.xlsx",
                "state": "partial",
                "records_created": 120,
                "records_skipped": 5,
            }
        )
        self._run(log, replay=True)
        self.assertEqual(log.records_created, 120, "the original run's orders must not be forgotten")
        self.assertEqual(log.records_skipped, 5)

    def test_replay_job_ignores_a_non_x24_log(self):
        x101 = self.env.ref("custom_retail_import.profile_retail_x101")
        log = self.env["retail.import.log"].create({"profile_id": x101.id, "filename": "x101.xlsx"})
        self.assertFalse(self.Executor._job_replay_x24_parked(log.id))

    def test_replay_job_without_parked_rows_does_nothing(self):
        log = self.env["retail.import.log"].create({"profile_id": self.profile.id, "filename": "x24.xlsx"})
        self.assertFalse(self.Executor._job_replay_x24_parked(log.id))
