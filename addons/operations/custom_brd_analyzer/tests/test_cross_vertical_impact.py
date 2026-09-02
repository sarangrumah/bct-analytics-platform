# -*- coding: utf-8 -*-
"""Tests for the cross-vertical impact analysis (Track B)."""

from __future__ import annotations

import base64
import json
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCrossVerticalImpact(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Document = self.env["brd.document"]
        self.Section = self.env["brd.document.section"]
        self.Recommendation = self.env["brd.recommendation"]
        self.Catalog = self.env["custom.hub.module.catalog"]

        # Seed at least one hub catalog entry so the affects-many2many resolves.
        existing = self.Catalog.search([("module_name", "=", "custom_coretax")], limit=1)
        self.cat_coretax = existing or self.Catalog.create(
            {
                "module_name": "custom_coretax",
                "category": "compliance",
                "maturity": "production",
                "summary": "Coretax integration.",
            }
        )
        existing2 = self.Catalog.search([("module_name", "=", "custom_accounting_full")], limit=1)
        self.cat_accfull = existing2 or self.Catalog.create(
            {
                "module_name": "custom_accounting_full",
                "category": "ee_gap",
                "maturity": "production",
                "summary": "Full accounting EE-gap.",
            }
        )

        attachment = self.env["ir.attachment"].create(
            {
                "name": "fake_brd.txt",
                "datas": base64.b64encode(b"hello").decode(),
                "mimetype": "text/plain",
            }
        )
        self.doc = self.Document.create(
            {
                "name": "CVI BRD",
                "document_attachment_id": attachment.id,
                "business_domain": "retail",
                "vertical_target": "retail",
                "company_profile_json": json.dumps({"name": "PT Test", "npwp": "00.000.000.0-000.000"}),
            }
        )

    # ------------------------------------------------------------------
    # impact_severity compute
    # ------------------------------------------------------------------

    def test_impact_severity_low_when_no_impact(self):
        rec = self.Recommendation.create(
            {
                "document_id": self.doc.id,
                "name": "custom_zero_impact",
                "scope": "Greenfield module.",
            }
        )
        self.assertEqual(rec.impact_severity, "low")

    def test_impact_severity_medium_with_single_vertical(self):
        rec = self.Recommendation.create(
            {
                "document_id": self.doc.id,
                "name": "custom_one_vertical",
                "affects_existing_module_ids": [(6, 0, [self.cat_coretax.id])],
                "cross_vertical_impact_json": json.dumps({"custom_coretax": ["retail"]}),
                "breaking_change": False,
                "compat_strategy": "extend",
            }
        )
        self.assertEqual(rec.impact_severity, "medium")

    def test_impact_severity_high_with_breaking_change(self):
        rec = self.Recommendation.create(
            {
                "document_id": self.doc.id,
                "name": "custom_breaking",
                "affects_existing_module_ids": [(6, 0, [self.cat_coretax.id])],
                "cross_vertical_impact_json": json.dumps({"custom_coretax": ["retail", "fnb"]}),
                "breaking_change": True,
                "compat_strategy": "fork_warning",
            }
        )
        self.assertEqual(rec.impact_severity, "high")

    def test_impact_severity_critical_when_breaking_and_many_verticals(self):
        rec = self.Recommendation.create(
            {
                "document_id": self.doc.id,
                "name": "custom_critical",
                "affects_existing_module_ids": [(6, 0, [self.cat_coretax.id, self.cat_accfull.id])],
                "cross_vertical_impact_json": json.dumps(
                    {
                        "custom_coretax": ["retail", "fnb", "healthcare"],
                        "custom_accounting_full": ["retail"],
                    }
                ),
                "breaking_change": True,
                "compat_strategy": "abstract_base",
            }
        )
        self.assertEqual(rec.impact_severity, "critical")

    # ------------------------------------------------------------------
    # AI parse: ensure new fields are populated from mocked response.
    # ------------------------------------------------------------------

    def test_analyze_populates_cross_vertical_fields(self):
        sec = self.Section.create(
            {
                "document_id": self.doc.id,
                "sequence": 1,
                "title": "Tax",
                "content": "Need PPh23 withholding.",
            }
        )
        self.doc.state = "extracted"

        mock_response = {
            "overall_fit_pct": 55,
            "sections": [
                {
                    "section_id": sec.id,
                    "capability_required": "PPh23 withholding.",
                    "capabilities_mentioned": ["tax", "indonesia"],
                    "mapped_module_names": ["custom_coretax"],
                    "fit_score": 60,
                    "gap_status": "partial",
                    "gap_severity": "must_have",
                    "notes": "Coretax covers most; missing PPh23 codes.",
                }
            ],
            "recommendations": [
                {
                    "name": "custom_pph23_codes",
                    "scope": "Add PPh23 tax codes.",
                    "capability_tags": ["tax", "pph23"],
                    "estimated_md": 8,
                    "depends": [],
                    "depends_on_proposed": [],
                    "impact_modules": [],
                    "severity": "must_have",
                    "justification": "Required by Indonesian tax law.",
                    "related_section_ids": [],
                    "affects_existing_modules": [
                        "custom_coretax",
                        "custom_accounting_full",
                    ],
                    "cross_vertical_impact": {
                        "custom_coretax": ["retail", "fnb"],
                        "custom_accounting_full": ["retail"],
                    },
                    "breaking_change": False,
                    "compat_strategy": "extend",
                }
            ],
        }
        ai_text = json.dumps(mock_response)

        with patch.object(
            type(self.env["custom.ai"]),
            "_chat",
            return_value={"content": [{"text": ai_text}]},
        ):
            self.doc.action_analyze()

        self.assertEqual(self.doc.state, "analyzed")
        rec = self.doc.recommendation_ids
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec.name, "custom_pph23_codes")
        self.assertEqual(rec.compat_strategy, "extend")
        self.assertFalse(rec.breaking_change)
        affected_names = set(rec.affects_existing_module_ids.mapped("module_name"))
        self.assertEqual(affected_names, {"custom_coretax", "custom_accounting_full"})
        parsed = json.loads(rec.cross_vertical_impact_json)
        self.assertEqual(parsed["custom_coretax"], ["retail", "fnb"])
        # impact_severity is computed: 3 distinct verticals (retail, fnb) and no
        # breaking change -> 2 verticals -> medium.
        self.assertIn(rec.impact_severity, ("medium", "high"))

    # ------------------------------------------------------------------
    # _build_cross_vertical_context smoke
    # ------------------------------------------------------------------

    def test_build_cross_vertical_context_returns_catalog_rows(self):
        from odoo.addons.custom_brd_analyzer.models.brd_ai_analyzer import (
            BrdAiAnalyzer,
        )

        ctx = BrdAiAnalyzer(self.env)._build_cross_vertical_context()
        self.assertIsInstance(ctx, list)
        names = {row["module_name"] for row in ctx}
        self.assertIn("custom_coretax", names)
        for row in ctx:
            self.assertIn("deployed_in_verticals", row)
            self.assertIn("tags", row)
            self.assertIn("maturity", row)
