# -*- coding: utf-8 -*-
"""AI OCR: payload assembly + response parsing (no live gateway call)."""

from __future__ import annotations

import base64

from odoo.tests.common import TransactionCase


class TestOcrPayload(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Expense = cls.env["hr.expense"]
        cls.employee = cls.env["hr.employee"].create({"name": "OCR Tester"})
        cls.product = cls.env["product.product"].create(
            {
                "name": "Meals",
                "default_code": "MEAL",
                "can_be_expensed": True,
            }
        )
        cls.expense = cls.Expense.create(
            {
                "name": "Lunch receipt",
                "employee_id": cls.employee.id,
                "product_id": cls.product.id,
                "total_amount": 0.0,
            }
        )

    def _attach_dummy_receipt(self, expense):
        raw = b"%PDF-1.4 fake receipt bytes"
        return self.env["ir.attachment"].create(
            {
                "name": "receipt.pdf",
                "res_model": expense._name,
                "res_id": expense.id,
                "type": "binary",
                "datas": base64.b64encode(raw).decode("ascii"),
                "mimetype": "application/pdf",
            }
        )

    def test_payload_includes_image_base64_when_attached(self):
        self._attach_dummy_receipt(self.expense)
        payload = self.expense._custom_ai_payload()
        self.assertEqual(payload["task"], "extract_receipt")
        self.assertTrue(payload["image_base64"])
        self.assertEqual(payload["attachment_mimetype"], "application/pdf")
        self.assertEqual(payload["attachment_name"], "receipt.pdf")
        # Round-trip the base64 to confirm it decodes
        decoded = base64.b64decode(payload["image_base64"])
        self.assertIn(b"PDF", decoded)

    def test_payload_empty_image_when_no_attachment(self):
        # Fresh expense, no attachment
        exp = self.Expense.create(
            {
                "name": "Empty",
                "employee_id": self.employee.id,
                "product_id": self.product.id,
            }
        )
        payload = exp._custom_ai_payload()
        self.assertEqual(payload["image_base64"], "")

    def test_parse_response_populates_all_fields(self):
        response = {
            "amount": "125000",
            "tax_amount": "12500",
            "vendor": "Warung Sederhana",
            "date": "2025-01-15",
            "currency_code": "IDR",
            "confidence": 0.92,
            "ocr_text": "Warung Sederhana\nNasi Padang 125.000",
        }
        vals = self.Expense._parse_ai_receipt_response(response)
        self.assertEqual(vals["x_ai_extracted_amount"], 125000.0)
        self.assertEqual(vals["x_ai_extracted_tax_amount"], 12500.0)
        self.assertEqual(vals["x_ai_extracted_vendor"], "Warung Sederhana")
        self.assertEqual(vals["x_ai_extracted_date"], "2025-01-15")
        self.assertEqual(vals["x_ai_extracted_currency_code"], "IDR")
        self.assertAlmostEqual(vals["x_ai_confidence"], 0.92, places=2)
        self.assertIn("Warung", vals["x_receipt_ocr_text"])

    def test_parse_response_tolerates_garbage(self):
        self.assertEqual(self.Expense._parse_ai_receipt_response(None), {})
        self.assertEqual(self.Expense._parse_ai_receipt_response("not a dict"), {})
        # Partial dict — only present keys populate
        vals = self.Expense._parse_ai_receipt_response({"vendor": "Foo"})
        self.assertEqual(vals, {"x_ai_extracted_vendor": "Foo"})

    def test_parse_response_non_numeric_amount_ignored(self):
        vals = self.Expense._parse_ai_receipt_response({"amount": "not-a-number"})
        self.assertNotIn("x_ai_extracted_amount", vals)
