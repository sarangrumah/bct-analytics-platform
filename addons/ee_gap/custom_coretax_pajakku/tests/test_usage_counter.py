# -*- coding: utf-8 -*-
"""Usage meter atomic increment behaviour."""

from __future__ import annotations

from datetime import date

from .common import PajakkuCommon


class TestUsageCounter(PajakkuCommon):
    def test_get_current_creates_row_for_first_call(self):
        period = date.today().replace(day=1)
        # Clean slate
        existing = self.Usage.search(
            [
                ("company_id", "=", self.config.company_id.id),
                ("period", "=", period),
            ]
        )
        existing.unlink()
        row = self.Usage._get_current(self.config.company_id)
        self.assertTrue(row.id)
        self.assertEqual(row.api_calls, 0)

    def test_increment_persists(self):
        self.Usage.increment("api_calls", company=self.config.company_id, by=3)
        self.Usage.increment("faktur_submits", company=self.config.company_id, by=2)
        self.Usage.increment("bupot_submits", company=self.config.company_id, by=1)
        self.Usage.increment("errors", company=self.config.company_id, by=1)
        row = self.Usage._get_current(self.config.company_id)
        self.assertGreaterEqual(row.api_calls, 3)
        self.assertGreaterEqual(row.faktur_submits, 2)
        self.assertGreaterEqual(row.bupot_submits, 1)
        self.assertGreaterEqual(row.errors, 1)

    def test_unknown_kind_is_silent(self):
        # Shouldn't raise — just ignored
        self.Usage.increment("bogus_field", company=self.config.company_id)

    def test_one_row_per_company_per_month(self):
        self.Usage.increment("api_calls", company=self.config.company_id)
        self.Usage.increment("api_calls", company=self.config.company_id)
        rows = self.Usage.search(
            [
                ("company_id", "=", self.config.company_id.id),
                ("period", "=", date.today().replace(day=1)),
            ]
        )
        self.assertEqual(len(rows), 1)
