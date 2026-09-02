# -*- coding: utf-8 -*-
"""End-to-end tests over real HTTP, through the controller.

These exist because the model-level suite missed a whole class of bug. Every route
is ``auth="none"``, so a request arrives with **no user** — which means
``self.env.company`` is empty and any field defaulting to it silently becomes NULL.
The first real POST to the running server died on
``null value in column "company_id" violates not-null constraint``, while all 85
model-level tests passed, because those run as a logged-in user with a company.

So the contract here is deliberately about the things only a real request exercises:
the absence of a user, the HTTP status codes, and the auth headers.
"""

from __future__ import annotations

import json

from odoo.tests import tagged
from odoo.tests.common import HttpCase

from .common import item

_KEY = "test-key-aaaaaaaaaaaaaaaaaaaaaaaaaaaa"


@tagged("post_install", "-at_install")
class TestMdmHttp(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("custom_core.secure_endpoint.mdm.auth_mode", "api_key")
        icp.set_param("custom_core.secure_endpoint.mdm.api_keys", _KEY)
        icp.set_param("custom_core.secure_endpoint.mdm.allowed_cidrs", "127.0.0.1,::1")
        icp.set_param("retail_import.mdm_api_enabled", "1")
        icp.set_param("retail_import.mdm_dry_run", "0")
        # Process in-request, so these tests cover the whole pipeline -- mapping and
        # the product write included -- under the no-user conditions of a real call.
        # Deferring to a worker would leave exactly that part untested, which is how
        # the company_id and product.value.user_id crashes slipped through.
        icp.set_param("retail_import.mdm_sync_processing", "1")
        # No commit: HttpCase serves requests on the test cursor, so the handler sees
        # these uncommitted parameters -- and Odoo 19 forbids committing in a test.

    def _post(self, payload, key=_KEY, request_id=None):
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if request_id:
            headers["X-Request-Id"] = request_id
        return self.url_open("/api/mdm/products", data=json.dumps(payload).encode(), headers=headers, timeout=60)

    def _get(self, path, key=_KEY):
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        return self.url_open(path, headers=headers, timeout=60)

    # ------------------------------------------------------------------
    def test_ping_requires_a_valid_key(self):
        self.assertEqual(self._get("/api/mdm/ping").status_code, 200)
        self.assertEqual(self._get("/api/mdm/ping", key="wrong").status_code, 401)
        self.assertEqual(self._get("/api/mdm/ping", key=None).status_code, 401)

    def test_endpoint_states_which_system_it_is(self):
        """UAT and production differ only by a path prefix the caller cannot see.

        So the endpoint has to say which system it is, on the write path and not
        only on a health check — otherwise a misconfigured client has no way to
        detect that it is about to change live master data.
        """
        self.env["ir.config_parameter"].sudo().set_param("retail_import.mdm_environment", "uat")

        ping = self._get("/api/mdm/ping").json()["data"]
        self.assertEqual(ping["environment"], "uat")
        self.assertEqual(ping["database"], self.env.cr.dbname)

        accepted = self._post(item(), request_id="http-env").json()["data"]
        self.assertEqual(accepted["environment"], "uat")
        self.assertEqual(accepted["database"], self.env.cr.dbname)

    def test_unset_environment_does_not_read_as_reassuring(self):
        self.env["ir.config_parameter"].sudo().search([("key", "=", "retail_import.mdm_environment")]).unlink()
        self.assertEqual(self._get("/api/mdm/ping").json()["data"]["environment"], "unknown")

    def test_post_without_a_user_still_sets_the_company(self):
        """The regression test for the NOT NULL crash on company_id.

        auth="none" means env.company is empty, so the company has to be resolved
        from the X101 profile rather than defaulted from a user that is not there.
        """
        response = self._post(item(), request_id="http-1")
        self.assertEqual(response.status_code, 202, response.text)

        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["data"]["requestId"])
        self.assertEqual(body["data"]["accepted"], 1)
        self.assertFalse(body["data"]["duplicate"])

        record = self.env["retail.mdm.request"].sudo().search([("request_id", "=", body["data"]["requestId"])])
        self.assertTrue(record, "the request must have been staged")
        self.assertTrue(record.company_id, "company_id must never be NULL")
        self.assertTrue(record.source_ip)

        # ...and the product really lands, keyed on udf2.
        self.assertEqual(record.state, "partial" if record.review_count else "done")
        variant = self.env["product.product"].search([("default_code", "=", "002IJ002703228")])
        self.assertEqual(len(variant), 1, "the variant must be created by a no-user request")
        self.assertEqual(variant.mdm_sku_code, "002IJ-00273228")

    def test_duplicate_post_answers_200_not_202(self):
        first = self._post(item(), request_id="http-dup")
        self.assertEqual(first.status_code, 202)
        second = self._post(item(), request_id="http-dup")
        self.assertEqual(second.status_code, 200, "a retry is not new work, and not an error")
        self.assertTrue(second.json()["data"]["duplicate"])
        self.assertEqual(second.json()["data"]["requestId"], first.json()["data"]["requestId"])

    def test_array_payload_accepted(self):
        second = item(skuCode="002IJ-00273230", udf2="002IJ002703230", size="32 30", upc_ean="5401231363523")
        response = self._post([item(), second], request_id="http-array")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["data"]["accepted"], 2)

    def test_malformed_and_unkeyed_payloads_are_refused(self):
        headers = {"Authorization": f"Bearer {_KEY}", "Content-Type": "application/json"}
        broken = self.url_open("/api/mdm/products", data=b"{not json", headers=headers, timeout=60)
        # Unparseable JSON never reaches the handler: the json2 dispatcher parses the
        # body first and answers with its own werkzeug BadRequest shape. Still a 400,
        # but NOT our {status,data,error} envelope -- worth pinning, because an
        # integrator debugging a broken payload will see a different response here.
        self.assertEqual(broken.status_code, 400)
        self.assertNotIn("error", broken.json())

        empty = self._post([], request_id="http-empty")
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(empty.json()["error"]["code"], "EMPTY_PAYLOAD")

        unkeyed = self._post(item(skuCode=None, udf2=None), request_id="http-unkeyed")
        self.assertEqual(unkeyed.status_code, 400)
        self.assertEqual(unkeyed.json()["error"]["code"], "MISSING_SKU_CODE")
        self.assertFalse(
            self.env["retail.mdm.request"].sudo().search([("dedupe_key", "=", "http-unkeyed")]),
            "a batch we cannot key must not be staged at all",
        )

    def test_disabled_service_answers_503(self):
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("retail_import.mdm_api_enabled", "0")
        try:
            response = self._post(item(), request_id="http-off")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "SERVICE_DISABLED")
        finally:
            icp.set_param("retail_import.mdm_api_enabled", "1")

    def test_lookup_and_unknown_request(self):
        missing = self._get("/api/mdm/products/lookup")
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()["error"]["code"], "MISSING_QUERY")

        unknown = self._get("/api/mdm/requests/does-not-exist")
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unknown.json()["error"]["code"], "UNKNOWN_REQUEST")

        found = self._get("/api/mdm/products/lookup?sku=NO-SUCH-SKU")
        self.assertEqual(found.status_code, 200)
        self.assertFalse(found.json()["data"]["found"])

    def test_pending_listing(self):
        response = self._get("/api/mdm/pending?limit=5")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertIn("items", data)
        self.assertIn("total", data)
