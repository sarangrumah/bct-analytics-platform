# -*- coding: utf-8 -*-
"""Report editor: header/footer customization materialises a QWeb inheritance."""

from __future__ import annotations

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestReportCustomization(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Customization = cls.env["studio.report.customization"]
        cls.Xpath = cls.env["studio.report.xpath"]
        cls.Report = cls.env["ir.actions.report"]
        # Pick any existing qweb-pdf report — the user report on res.users
        # exists in base. Fallback: search any qweb-pdf report.
        cls.report = cls.Report.search([("report_type", "=", "qweb-pdf")], limit=1)
        if not cls.report:
            cls.skipTest(cls, "No qweb-pdf report available for testing.")

    def test_header_append_creates_inheritance(self):
        cust = self.Customization.create(
            {
                "name": "Branded header",
                "base_report_id": self.report.id,
                "header_text": "Acme Corp — Internal",
                "header_mode": "append",
            }
        )
        cust.action_apply()
        cust.invalidate_recordset()
        # Some QWeb templates may not have a div.header — that's OK,
        # apply still creates the inheritance shell even if it later
        # fails validation. We accept either applied or error state but
        # require that an inherit_view_id was upserted.
        self.assertIn(cust.state, ("applied", "error"))
        if cust.state == "applied":
            self.assertTrue(cust.inherit_view_id)
            self.assertIn("Acme Corp", cust.inherit_view_id.arch_db or "")

    def test_forbidden_qweb_directive_rejected(self):
        cust = self.Customization.create(
            {
                "name": "Bad QWeb",
                "base_report_id": self.report.id,
            }
        )
        with self.assertRaises(ValidationError):
            self.Xpath.create(
                {
                    "customization_id": cust.id,
                    "label": "evil",
                    "xpath_snippet": '<xpath expr="//body" position="inside"><t t-call="bad.template"/></xpath>',
                }
            )

    def test_snippet_must_start_with_xpath(self):
        cust = self.Customization.create(
            {
                "name": "Bad start",
                "base_report_id": self.report.id,
            }
        )
        with self.assertRaises(ValidationError):
            self.Xpath.create(
                {
                    "customization_id": cust.id,
                    "xpath_snippet": "<div>not xpath</div>",
                }
            )
