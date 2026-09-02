# -*- coding: utf-8 -*-
"""Transaction lifecycle helpers + retry behaviour."""

from __future__ import annotations

from .common import PajakkuCommon


class TestTransactionState(PajakkuCommon):
    def _mk_tx(self, state: str = "queued", retry_count: int = 0) -> "models.Model":  # noqa: F821 — `models` is a quoted forward reference; Odoo resolves it at type-check time
        return self.Tx.create(
            {
                "company_id": self.config.company_id.id,
                "config_id": self.config.id,
                "transaction_type": "efaktur_keluaran",
                "state": state,
                "retry_count": retry_count,
                "payload": __import__("base64").b64encode(b"<xml/>"),
                "payload_filename": "p.xml",
            }
        )

    def test_mark_submitting_sets_state(self):
        tx = self._mk_tx()
        tx.mark_submitting()
        self.assertEqual(tx.state, "submitting")
        self.assertTrue(tx.submitted_at)

    def test_mark_submitted_records_uuid(self):
        tx = self._mk_tx()
        tx.mark_submitted("uuid-abc")
        self.assertEqual(tx.state, "submitted")
        self.assertEqual(tx.external_uuid, "uuid-abc")

    def test_mark_approved_records_nsfp(self):
        tx = self._mk_tx(state="submitted")
        tx.external_uuid = "uuid-x"
        tx.mark_approved("0000123456789012")
        self.assertEqual(tx.state, "approved")
        self.assertEqual(tx.nsfp, "0000123456789012")
        self.assertTrue(tx.completed_at)

    def test_mark_rejected_records_code_and_message(self):
        tx = self._mk_tx(state="submitted")
        tx.mark_rejected("E001", "NPWP tidak valid")
        self.assertEqual(tx.state, "rejected")
        self.assertEqual(tx.djp_status_code, "E001")
        self.assertIn("NPWP", tx.djp_message or "")

    def test_mark_error_increments_retry(self):
        tx = self._mk_tx(retry_count=2)
        tx.mark_error("transport: timeout")
        self.assertEqual(tx.state, "error")
        self.assertEqual(tx.retry_count, 3)
        self.assertIn("timeout", tx.last_error or "")

    def test_action_retry_requeues_errored(self):
        tx = self._mk_tx(state="error", retry_count=2)
        tx.last_error = "boom"
        tx.action_retry()
        self.assertEqual(tx.state, "queued")
        self.assertFalse(tx.last_error)
        # Retry count is preserved (so the cron respects max retry cap)
        self.assertEqual(tx.retry_count, 2)

    def test_action_retry_skips_active_states(self):
        tx = self._mk_tx(state="submitting")
        tx.action_retry()
        self.assertEqual(tx.state, "submitting")  # untouched
