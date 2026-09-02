# -*- coding: utf-8 -*-
"""Tests for custom_brd_analyzer."""

from __future__ import annotations

import base64
import json
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


MOCK_AI_RESPONSE = {
    "overall_fit_pct": 62,
    "sections": [
        {
            "section_id": None,  # filled at runtime in setUp
            "capability_required": "Rental contract with prorata billing.",
            "capabilities_mentioned": ["rental", "indonesian-tax"],
            "mapped_module_names": ["custom_rental"],
            "fit_score": 80,
            "gap_status": "partial",
            "gap_severity": "must_have",
            "notes": "Rental covers the contract; prorata needs a tweak.",
        },
        {
            "section_id": None,
            "capability_required": "Real-time WMS with HHT scanning.",
            "capabilities_mentioned": ["wms", "barcode-scan", "hht"],
            "mapped_module_names": [],
            "fit_score": 10,
            "gap_status": "missing",
            "gap_severity": "must_have",
            "notes": "No WMS in hub yet.",
        },
    ],
    "recommendations": [
        {
            "name": "custom_wms_hht",
            "scope": "Warehouse + HHT scanning for inbound/outbound.",
            "capability_tags": ["wms", "hht", "barcode-scan"],
            "estimated_md": 30,
            "depends": ["custom_core"],
            "depends_on_proposed": [],
            "impact_modules": [],
            "severity": "must_have",
            "justification": "Required for warehouse staff productivity.",
            "related_section_ids": [],
        },
        {
            "name": "custom_rental_prorata",
            "scope": "Add daily proration to custom_rental.",
            "capability_tags": ["rental", "accounting"],
            "estimated_md": 5,
            "depends": ["custom_rental"],
            "depends_on_proposed": [],
            "impact_modules": ["custom_rental"],
            "severity": "should_have",
            "justification": "Customer asks for partial-month invoicing.",
            "related_section_ids": [],
        },
    ],
}


@tagged("post_install", "-at_install")
class TestBrdAnalyzer(TransactionCase):
    def _seed_entry(self, vals):
        """Upsert a capability entry, mirroring the catalog scan's own logic."""
        existing = self.Entry.search([("module_name", "=", vals["module_name"])], limit=1)
        if existing:
            existing.write(vals)
            return existing
        return self.Entry.create(vals)

    def setUp(self):
        super().setUp()
        self.Document = self.env["brd.document"]
        self.Section = self.env["brd.document.section"]
        self.Entry = self.env["custom.module.capability.entry"]
        self.Tag = self.env["custom.module.capability.tag"]

        # Seed catalog entries so AI mapping resolves to real records. The
        # catalog scan that runs on install already holds a row for every
        # module present, so upsert rather than create — module_name is
        # unique, and creating blindly collides on any database that has been
        # scanned (which is every real one).
        self.entry_rental = self._seed_entry(
            {
                "module_name": "custom_rental",
                "category": "ee_gap",
                "summary": "Rental management with Indonesian tax integration.",
                "maturity": "production",
                "depends": ["custom_core"],
                "models_own": ["rental.contract"],
            }
        )
        self.entry_core = self._seed_entry(
            {
                "module_name": "custom_core",
                "category": "core",
                "summary": "Platform core: security, audit chain.",
                "maturity": "production",
                "models_own": ["custom.security"],
            }
        )

        # Synthetic attachment (plain text — we'll bypass extraction by creating
        # sections directly for most tests).
        self.attachment = self.env["ir.attachment"].create(
            {
                "name": "fake_brd.txt",
                "datas": base64.b64encode(b"hello world").decode(),
                "mimetype": "text/plain",
            }
        )

        self.doc = self.Document.create(
            {
                "name": "Test BRD",
                "document_attachment_id": self.attachment.id,
                "business_domain": "logistics",
            }
        )
        self.sec1 = self.Section.create(
            {"document_id": self.doc.id, "sequence": 1, "title": "Rental", "content": "We need rental contracts."}
        )
        self.sec2 = self.Section.create(
            {"document_id": self.doc.id, "sequence": 2, "title": "Warehouse", "content": "We need WMS with HHT."}
        )
        self.doc.state = "extracted"

    # ------------------------------------------------------------------

    def _mock_response_for_doc(self):
        # Bind the section ids that exist now (TestBrdAnalyzer creates fresh ones each test).
        resp = json.loads(json.dumps(MOCK_AI_RESPONSE))
        resp["sections"][0]["section_id"] = self.sec1.id
        resp["sections"][1]["section_id"] = self.sec2.id
        return resp

    # ------------------------------------------------------------------
    # Test 1: AI analysis pipeline end-to-end (with mocked gateway).
    # ------------------------------------------------------------------

    def test_analyze_creates_records(self):
        resp = self._mock_response_for_doc()
        ai_text = json.dumps(resp)

        with patch.object(type(self.env["custom.ai"]), "_chat", return_value={"content": [{"text": ai_text}]}):
            self.doc.action_analyze()

        self.assertEqual(self.doc.state, "analyzed")
        self.assertEqual(len(self.doc.analysis_ids), 2)
        self.assertEqual(len(self.doc.recommendation_ids), 2)
        # Rental analysis must point at the rental hub module.
        rental_analysis = self.doc.analysis_ids.filtered(lambda a: a.section_id == self.sec1)
        self.assertIn(self.entry_rental, rental_analysis.mapped_module_ids)
        # Overall fit is weighted-by-severity.
        self.assertGreater(self.doc.overall_fit_pct, 0)
        self.assertLess(self.doc.overall_fit_pct, 100)

    # ------------------------------------------------------------------
    # Test 2: malformed JSON triggers retry.
    # ------------------------------------------------------------------

    def test_analyze_retries_on_bad_json(self):
        good = json.dumps(self._mock_response_for_doc())
        responses = iter(
            [
                {"content": [{"text": "not json at all"}]},
                {"content": [{"text": good}]},
            ]
        )

        def fake_chat(*args, **kwargs):
            return next(responses)

        with patch.object(type(self.env["custom.ai"]), "_chat", side_effect=fake_chat):
            self.doc.action_analyze()

        self.assertEqual(self.doc.state, "analyzed")
        self.assertTrue(self.doc.analysis_ids)

    # ------------------------------------------------------------------
    # Test 3: capability catalog scan creates entries from manifests.
    # ------------------------------------------------------------------

    def test_catalog_scan(self):
        # We just need ``custom_core`` and ``custom_brd_analyzer`` themselves to be picked up.
        self.Entry.search([]).unlink()
        count = self.Entry._scan_all_modules()
        self.assertGreater(count, 0)
        ours = self.Entry.search([("module_name", "=", "custom_brd_analyzer")], limit=1)
        self.assertTrue(ours, "Self-entry should exist after scan")
        self.assertEqual(ours.category, "operations")
        self.assertIn("brd.document", ours.models_own or [])

    # ------------------------------------------------------------------
    # Test 4: push-to-project creates a linked task.
    # ------------------------------------------------------------------

    def test_push_to_project(self):
        rec = self.env["brd.recommendation"].create(
            {
                "document_id": self.doc.id,
                "name": "custom_demo_push",
                "scope": "demo",
                "severity": "should_have",
                "estimated_md": 3,
                "depends_on_module_ids": [(6, 0, [self.entry_core.id])],
            }
        )
        rec.action_push_to_project()
        self.assertTrue(rec.project_task_id, "Project task should be created")
        self.assertEqual(rec.state, "in_backlog")
        self.assertEqual(rec.project_task_id.brd_recommendation_id, rec)
        self.assertEqual(rec.project_task_id.project_id.name, "Hub Backlog - BRD")

    # ------------------------------------------------------------------
    # Test 5: approval flow trigger on state transition.
    # ------------------------------------------------------------------

    def test_approval_trigger(self):
        # Provide an active matrix for brd.document; the engine will create a request.
        model = self.env["ir.model"].search([("model", "=", "brd.document")], limit=1)
        matrix = self.env["approval.matrix"].create(
            {
                "name": "BRD review",
                "model_id": model.id,
                "active": True,
            }
        )
        # Run a fake analysis so we can transition into 'analyzed' first.
        resp = json.dumps(self._mock_response_for_doc())
        with patch.object(type(self.env["custom.ai"]), "_chat", return_value={"content": [{"text": resp}]}):
            self.doc.action_analyze()
        self.doc.action_request_review()
        self.assertEqual(self.doc.state, "reviewed")
        # An approval.request was created.
        req = self.env["approval.request"].search(
            [("res_model", "=", "brd.document"), ("res_id", "=", self.doc.id)], limit=1
        )
        self.assertTrue(req, "Approval request should be created on review")
        self.assertEqual(req.matrix_id, matrix)

    # ------------------------------------------------------------------
    # Test 6: extractor missing-dependency path emits a user-friendly error.
    # ------------------------------------------------------------------

    def test_extract_dependency_error(self):
        from odoo.addons.custom_brd_analyzer.models import brd_extractor as bex

        # Force all extractors to "not installed".
        with patch.object(bex, "fitz", None), patch.object(bex, "docx", None), patch.object(bex, "Presentation", None):
            extractor = bex.BrdExtractor()
            with self.assertRaises(bex.ExtractorDependencyError):
                extractor.extract(b"%PDF-1.4", mime="application/pdf", filename="x.pdf")
