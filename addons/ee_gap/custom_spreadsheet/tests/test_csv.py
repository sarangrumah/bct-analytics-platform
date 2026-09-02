# -*- coding: utf-8 -*-
"""CSV import / export round-trip tests."""

import base64
import json

from odoo.tests.common import TransactionCase


class TestSpreadsheetCsv(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Workbook = self.env["custom.spreadsheet.workbook"]
        self.Wizard = self.env["custom.spreadsheet.import.wizard"]

    def _make_wb(self, name="WB"):
        return self.Workbook.create({"name": name})

    def test_csv_import_populates_cells(self):
        wb = self._make_wb()
        csv_bytes = b"a,b,c\n1,2,3\n4,5,6\n"
        wiz = self.Wizard.create(
            {
                "workbook_id": wb.id,
                "csv_file": base64.b64encode(csv_bytes),
                "csv_filename": "test.csv",
                "sheet_name": "Sheet1",
                "delimiter": ",",
                "has_header": True,
            }
        )
        wiz.action_import()
        data = json.loads(wb.data_json)
        sheet = data["sheets"][0]
        self.assertEqual(sheet["name"], "Sheet1")
        cells = sheet["cells"]
        self.assertEqual(cells["0_0"], "a")
        self.assertEqual(cells["0_2"], "c")
        self.assertEqual(cells["2_1"], "5")

    def test_csv_export_returns_attachment_action(self):
        wb = self._make_wb()
        # Pre-populate with a 2x3 grid using the import wizard
        csv_bytes = b"h1,h2,h3\nx,y,z\n"
        wiz = self.Wizard.create(
            {
                "workbook_id": wb.id,
                "csv_file": base64.b64encode(csv_bytes),
                "csv_filename": "test.csv",
                "sheet_name": "Sheet1",
                "delimiter": ",",
                "has_header": True,
            }
        )
        wiz.action_import()
        action = wb.action_export_csv()
        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertIn("/web/content/", action["url"])
        # Verify the attachment exists and has CSV content
        att = self.env["ir.attachment"].search(
            [
                ("res_model", "=", "custom.spreadsheet.workbook"),
                ("res_id", "=", wb.id),
            ],
            limit=1,
            order="id desc",
        )
        self.assertTrue(att)
        content = base64.b64decode(att.datas).decode("utf-8")
        self.assertIn("h1,h2,h3", content)
        self.assertIn("x,y,z", content)

    def test_csv_roundtrip_preserves_values(self):
        wb = self._make_wb()
        original = b"col_a,col_b\nfoo,bar\n10,20\n"
        wiz = self.Wizard.create(
            {
                "workbook_id": wb.id,
                "csv_file": base64.b64encode(original),
                "csv_filename": "rt.csv",
                "sheet_name": "Sheet1",
                "delimiter": ",",
                "has_header": True,
            }
        )
        wiz.action_import()
        wb.action_export_csv()
        att = self.env["ir.attachment"].search(
            [
                ("res_model", "=", "custom.spreadsheet.workbook"),
                ("res_id", "=", wb.id),
            ],
            limit=1,
            order="id desc",
        )
        exported = base64.b64decode(att.datas).decode("utf-8").replace("\r\n", "\n")
        self.assertIn("col_a,col_b", exported)
        self.assertIn("foo,bar", exported)
        self.assertIn("10,20", exported)

    def test_version_snapshot_on_write(self):
        wb = self._make_wb()
        # Snapshot baseline (default data_json)
        initial_count = len(wb.version_ids)
        wb.write({"data_json": '{"sheets":[{"name":"Sheet1","cells":{"0_0":"x"}}]}'})
        self.assertEqual(len(wb.version_ids), initial_count + 1)
        # Same value -> no new snapshot
        wb.write({"data_json": '{"sheets":[{"name":"Sheet1","cells":{"0_0":"x"}}]}'})
        self.assertEqual(len(wb.version_ids), initial_count + 1)
        # Different value -> snapshot
        wb.write({"data_json": '{"sheets":[{"name":"Sheet1","cells":{"0_0":"y"}}]}'})
        self.assertEqual(len(wb.version_ids), initial_count + 2)

    def test_version_restore(self):
        wb = self._make_wb()
        wb.write({"data_json": '{"sheets":[{"name":"Sheet1","cells":{"0_0":"v1"}}]}'})
        wb.write({"data_json": '{"sheets":[{"name":"Sheet1","cells":{"0_0":"v2"}}]}'})
        first = wb.version_ids.sorted(lambda v: v.version_no)[0]
        prev_count = len(wb.version_ids)
        first.action_restore()
        # restore should NOT itself create a new version
        self.assertEqual(len(wb.version_ids), prev_count)
        # restored value
        data = json.loads(wb.data_json)
        self.assertEqual(
            data["sheets"][0]["cells"]["0_0"],
            first.data_json_snapshot and json.loads(first.data_json_snapshot)["sheets"][0]["cells"]["0_0"],
        )

    def test_share_token_generation(self):
        wb = self._make_wb()
        self.assertFalse(wb.share_token)
        wb.action_generate_share_token()
        self.assertTrue(wb.share_token)
        self.assertIn("/custom_spreadsheet/share/", wb.share_url or "")
        wb.action_revoke_share_token()
        self.assertFalse(wb.share_token)
