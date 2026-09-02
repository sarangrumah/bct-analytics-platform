# -*- coding: utf-8 -*-
"""Unit tests for custom_coretax_bupot."""

from __future__ import annotations

import base64
from xml.etree import ElementTree as ET

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_coretax_bupot")
class TestBupot(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Bupot = cls.env["custom.bupot.unifikasi"]
        cls.BupotLine = cls.env["custom.bupot.unifikasi.line"]
        cls.period = cls.Bupot.create({"month": "5", "year": "2026"})

    def _mk_line(self, **kw):
        defaults = {
            "bupot_id": self.period.id,
            "pph_type": "23",
            "cuttee_name": "PT Vendor",
            "cuttee_npwp": "0123456789012345",
            "gross_amount": 1_000_000.0,
            "dpp_amount": 1_000_000.0,
            "rate": 2.0,
            "withheld_amount": 20_000.0,
        }
        defaults.update(kw)
        return self.BupotLine.create(defaults)

    def test_01_create_period_with_three_lines(self):
        self._mk_line()
        self._mk_line(pph_type="22", rate=2.5, withheld_amount=25_000.0)
        self._mk_line(pph_type="4_2", rate=10.0, withheld_amount=100_000.0)
        self.assertEqual(self.period.line_count, 3)
        self.assertAlmostEqual(self.period.total_withheld, 145_000.0, places=2)

    def test_02_invalid_npwp_rejected(self):
        with self.assertRaises(Exception):
            self._mk_line(cuttee_npwp="ABC123")  # non-digits
        with self.assertRaises(Exception):
            self._mk_line(cuttee_npwp="123")  # too short

    def test_03_generate_xml_and_validate_structure(self):
        self._mk_line()
        self._mk_line(pph_type="26", rate=20.0, withheld_amount=200_000.0)
        wizard = self.env["custom.bupot.xml.export.wizard"].create({"bupot_id": self.period.id})
        wizard.action_generate()
        self.assertTrue(wizard.output_file)
        self.assertEqual(self.period.state, "generated")
        xml_bytes = base64.b64decode(wizard.output_file)
        # noqa: S314 - the XML being parsed is what this test just generated two
        # lines above, not untrusted input. An XXE hardening pass here would be
        # testing the parser rather than the wizard.
        root = ET.fromstring(xml_bytes)  # noqa: S314
        self.assertEqual(root.tag, "BuktiPotongUnifikasi")
        self.assertEqual(root.attrib.get("version"), "2.0")
        bupots = root.findall("Bupot")
        self.assertEqual(len(bupots), 2)

    def test_04_csv_upload_assigns_numbers_and_promotes_state(self):
        l1 = self._mk_line()
        l2 = self._mk_line(pph_type="22", rate=2.5, withheld_amount=25_000.0)
        # Pretend export + submit happened.
        self.period.state = "submitted"
        csv_body = f"internal_ref,bupot_number\n{l1.internal_ref},2310000000001\n{l2.internal_ref},2310000000002\n"
        wizard = self.env["custom.bupot.number.upload.wizard"].create(
            {
                "bupot_id": self.period.id,
                "csv_file": base64.b64encode(csv_body.encode("utf-8")),
                "csv_filename": "djp.csv",
            }
        )
        wizard.action_apply()
        self.assertEqual(l1.bupot_number, "2310000000001")
        self.assertEqual(l2.bupot_number, "2310000000002")
        self.assertEqual(self.period.state, "accepted")
        self.assertIn("Matched: 2", wizard.report)

    def test_05_csv_upload_reports_missing_refs(self):
        self._mk_line()
        self.period.state = "submitted"
        csv_body = "internal_ref,bupot_number\nNOPE,9999\n"
        wizard = self.env["custom.bupot.number.upload.wizard"].create(
            {
                "bupot_id": self.period.id,
                "csv_file": base64.b64encode(csv_body.encode("utf-8")),
            }
        )
        wizard.action_apply()
        self.assertIn("Missing refs: NOPE", wizard.report)
        # State stays submitted because not all lines have numbers
        self.assertEqual(self.period.state, "submitted")
