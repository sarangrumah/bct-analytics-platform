# -*- coding: utf-8 -*-
"""Circuit breaker state machine — open after N failures, closed on success."""

from __future__ import annotations

import time

from ..models import coretax_adapter_pajakku as adapter_mod
from .common import PajakkuCommon


class TestCircuitBreaker(PajakkuCommon):
    def setUp(self):
        super().setUp()
        # Reset module-level state between tests
        adapter_mod._CB_STATE.clear()
        adapter_mod._TOKEN_CACHE.clear()

    def test_breaker_closed_initially(self):
        self.assertFalse(adapter_mod._circuit_open(self.config.company_id.id))

    def test_breaker_opens_after_threshold_failures(self):
        cid = self.config.company_id.id
        for _i in range(adapter_mod._CB_THRESHOLD - 1):
            tripped = adapter_mod._circuit_record_failure(cid)
            self.assertFalse(tripped)
            self.assertFalse(adapter_mod._circuit_open(cid))
        tripped = adapter_mod._circuit_record_failure(cid)
        self.assertTrue(tripped)
        self.assertTrue(adapter_mod._circuit_open(cid))

    def test_breaker_resets_on_success(self):
        cid = self.config.company_id.id
        adapter_mod._circuit_record_failure(cid)
        adapter_mod._circuit_record_failure(cid)
        self.assertIn(cid, adapter_mod._CB_STATE)
        adapter_mod._circuit_record_success(cid)
        self.assertNotIn(cid, adapter_mod._CB_STATE)

    def test_breaker_auto_closes_after_window(self):
        cid = self.config.company_id.id
        # Force the breaker open with a past expiry
        adapter_mod._CB_STATE[cid] = {
            "fail_streak": adapter_mod._CB_THRESHOLD,
            "open_until": time.monotonic() - 1,  # already in the past
        }
        self.assertFalse(adapter_mod._circuit_open(cid))
