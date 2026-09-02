# -*- coding: utf-8 -*-
"""Adapter behaviour: envelope handling, query strings, pagination, auth retry."""

from __future__ import annotations

from odoo.tests import tagged

from ..models.esb_adapter import ENVELOPE_AUTH_STATUS, ENVELOPE_FAIL_STATUS, EsbApiError
from .common import EsbTestCase, load_fixture


@tagged("post_install", "-at_install", "esb")
class TestEsbAdapter(EsbTestCase):
    def test_envelope_fail_on_http_200_is_a_failure(self):
        """The trap this whole adapter exists for: ESB returns business errors
        with HTTP 200 and ``status: "fail"``."""
        self.given_logged_in()
        self.transport.register("POST", "/inventory/item-journal", load_fixture("validation_error"), status_code=200)

        resp = self.adapter().call("inventory/item-journal", payload={"x": 1})

        self.assertFalse(resp.ok, "HTTP 200 with status=fail must not be reported as success")
        self.assertEqual(resp.status_code, ENVELOPE_FAIL_STATUS)
        self.assertIn("EC03100400", resp.error)
        self.assertIn("Location must be warehouse or kitchen type", resp.error)
        self.assertEqual(resp.headers.get("X-Esb-Http-Status"), "200", "original HTTP status is preserved")

    def test_envelope_failure_is_not_retried(self):
        """A validation error is permanent — retrying only trips the breaker."""
        self.given_logged_in()
        self.core_config.retry_count = 3
        self.transport.register("POST", "/inventory/item-journal", load_fixture("validation_error"))

        self.adapter().call("inventory/item-journal", payload={"x": 1})

        self.assertEqual(self.transport.count("POST", "/inventory/item-journal"), 1)
        self.assertEqual(self.core_config.consecutive_failures, 0, "breaker must not count business errors")

    def test_envelope_ok_is_success(self):
        self.given_logged_in()
        self.transport.register("GET", "/branch", load_fixture("branch_list"))

        resp = self.adapter().get("branch")

        self.assertTrue(resp.ok)
        self.assertEqual(resp.status_code, 200)

    def test_get_builds_query_string_and_drops_empties(self):
        self.given_logged_in()
        self.transport.register("GET", "/report/stock-movement", load_fixture("stock_movement"))

        self.adapter().get(
            "report/stock-movement",
            {"startPeriod": "2026-07-01", "endPeriod": "2026-07-21", "branchCode": None, "location": ""},
        )

        url = self.transport.calls_to("GET", "/report/stock-movement")[0]["url"]
        self.assertIn("startPeriod=2026-07-01", url)
        self.assertIn("endPeriod=2026-07-21", url)
        self.assertNotIn("branchCode", url, "None params must be omitted, not sent blank")
        self.assertNotIn("location=", url)

    def test_rows_handles_bare_list_result(self):
        """/branch, /location and /units return `result` as a list, not result.data."""
        self.given_logged_in()
        self.transport.register("GET", "/branch", load_fixture("branch_list"))

        rows = self.adapter().get_rows("branch")

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["branchCode"], "HOF")

    def test_rows_handles_paged_result_and_null_data(self):
        self.given_logged_in()
        self.transport.register("GET", "/purpose", load_fixture("purpose_list"))
        self.transport.register("GET", "/report/stock-movement", load_fixture("empty_page"))

        self.assertEqual(len(self.adapter().get_rows("purpose")), 2)
        self.assertEqual(self.adapter().get_rows("report/stock-movement"), [], "count:0/data:null means no rows")

    def test_iter_rows_stops_on_short_page(self):
        self.given_logged_in()
        self.transport.register("GET", "/report/stock-movement", load_fixture("stock_movement"))

        rows = list(self.adapter().iter_rows("report/stock-movement", {"startPeriod": "2026-07-01"}))

        self.assertEqual(len(rows), 4)
        self.assertEqual(
            self.transport.count("GET", "/report/stock-movement"),
            1,
            "a page shorter than the limit ends pagination; no wasted second call",
        )

    def test_iter_rows_caps_limit_at_100(self):
        self.given_logged_in()
        self.transport.register("GET", "/report/stock-movement", load_fixture("stock_movement"))

        list(self.adapter().iter_rows("report/stock-movement", limit=500))

        self.assertIn("limit=100", self.transport.calls_to("GET", "/report/stock-movement")[0]["url"])

    def test_get_rows_raises_on_failure(self):
        self.given_logged_in()
        self.transport.register("GET", "/purpose", load_fixture("validation_error"))

        with self.assertRaises(EsbApiError):
            self.adapter().get_rows("purpose")

    def test_invalid_token_triggers_one_relogin_and_retry(self):
        """ESB evicts sessions, so a token can die before it expires."""
        self.given_logged_in()
        # First read is rejected, the second (after re-login) succeeds.
        self.transport.register("GET", "/branch", load_fixture("unauthorized"), times=1)
        self.transport.register("GET", "/branch", load_fixture("branch_list"))

        resp = self.adapter().get("branch")

        self.assertTrue(resp.ok, "the retry after re-login should succeed")
        self.assertEqual(self.transport.count("GET", "/branch"), 2)
        self.assertEqual(self.transport.count("POST", "/auth/login"), 2, "one initial login + one forced re-login")

    def test_auth_error_maps_to_401(self):
        self.given_logged_in()
        self.transport.register("GET", "/branch", load_fixture("unauthorized"))

        resp = self.adapter().get("branch")

        self.assertFalse(resp.ok)
        self.assertEqual(resp.status_code, ENVELOPE_AUTH_STATUS)

    def test_relogin_happens_only_once_per_call(self):
        """A permanently-dead credential must not loop."""
        self.given_logged_in()
        self.transport.register("GET", "/branch", load_fixture("unauthorized"))

        self.adapter().get("branch")

        self.assertEqual(self.transport.count("GET", "/branch"), 2, "one retry, not an infinite loop")

    def test_non_envelope_response_is_left_alone(self):
        """A gateway error or unexpected body must not be mistaken for an envelope."""
        self.given_logged_in()
        self.transport.register("GET", "/branch", {"unexpected": "shape"}, status_code=404)

        resp = self.adapter().get("branch")

        self.assertFalse(resp.ok)
        self.assertEqual(resp.status_code, 404, "a real HTTP failure keeps its own status code")
        self.assertNotIn("EC0", resp.error or "", "it must not be reported as an ESB envelope error")
