# -*- coding: utf-8 -*-
"""Load-from-model tests."""

import json

from odoo.tests.common import TransactionCase


class TestSpreadsheetLoadModel(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Workbook = self.env["custom.spreadsheet.workbook"]
        self.Partner = self.env["res.partner"]

    def test_load_partners_replace(self):
        # Ensure at least 2 partners exist
        self.Partner.create({"name": "Test Partner Load A"})
        self.Partner.create({"name": "Test Partner Load B"})
        wb = self.Workbook.create({"name": "Partners WB"})
        wb.action_load_from_model(
            model_name="res.partner",
            domain="[('name','ilike','Test Partner Load')]",
            fields_list="id, name",
            sheet_name="Partners",
        )
        data = json.loads(wb.data_json)
        sheet = next(s for s in data["sheets"] if s["name"] == "Partners")
        cells = sheet["cells"]
        # Header row
        self.assertEqual(cells["0_0"], "id")
        self.assertEqual(cells["0_1"], "name")
        # At least 2 data rows
        self.assertIn("1_1", cells)
        self.assertIn("2_1", cells)

    def test_load_with_invalid_fields_skipped(self):
        self.Partner.create({"name": "Partner X"})
        wb = self.Workbook.create({"name": "WB"})
        wb.action_load_from_model(
            model_name="res.partner",
            domain="[('name','=','Partner X')]",
            fields_list="name, nonexistent_field, id",
            sheet_name="P",
        )
        data = json.loads(wb.data_json)
        sheet = next(s for s in data["sheets"] if s["name"] == "P")
        # header should only have valid fields
        headers = [v for k, v in sheet["cells"].items() if k.startswith("0_")]
        self.assertIn("name", headers)
        self.assertIn("id", headers)
        self.assertNotIn("nonexistent_field", headers)

    def test_load_append_mode(self):
        self.Partner.create({"name": "Append A"})
        wb = self.Workbook.create({"name": "WB"})
        wb.action_load_from_model(
            model_name="res.partner",
            domain="[('name','=','Append A')]",
            fields_list="name",
            sheet_name="X",
        )
        rows_before = sum(
            1
            for s in json.loads(wb.data_json)["sheets"]
            if s["name"] == "X"
            for k in s["cells"].keys()
            if k.endswith("_0")
        )
        self.Partner.create({"name": "Append B"})
        wb.action_load_from_model(
            model_name="res.partner",
            domain="[('name','=','Append B')]",
            fields_list="name",
            sheet_name="X",
            append=True,
        )
        rows_after = sum(
            1
            for s in json.loads(wb.data_json)["sheets"]
            if s["name"] == "X"
            for k in s["cells"].keys()
            if k.endswith("_0")
        )
        self.assertGreater(rows_after, rows_before)

    def test_load_invalid_model_raises(self):
        wb = self.Workbook.create({"name": "WB"})
        with self.assertRaises(Exception):
            wb.action_load_from_model(
                model_name="not.a.model",
                domain="[]",
                fields_list="id",
            )
