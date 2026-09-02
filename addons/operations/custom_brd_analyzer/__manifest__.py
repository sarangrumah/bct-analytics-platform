# -*- coding: utf-8 -*-
{
    "name": "Custom BRD Analyzer",
    "summary": "AI-powered Business Requirements Document gap analyzer for the platform module hub",
    "description": """
Custom BRD Analyzer
===================

Upload a Business Requirements Document (PDF / DOCX / PPTX); the module:

1. **Extracts** structured text and section hierarchy with PyMuPDF /
   python-docx / python-pptx.
2. **Compares** the requirements against the live capability catalog of all
   installed hub modules (auto-scanned from manifests, ``_name``/``_inherit``
   and controller routes).
3. **Analyses** each section with the platform AI gateway
   (``custom_ai_bridge``) using Anthropic prompt caching on the (rarely
   changing) capability catalog.
4. **Recommends** a list of new ``custom_<x>`` modules to fill the gap, with
   scope, dependencies on existing hub modules, estimated man-days and
   severity.
5. **Workflow** — draft → extracted → analysed → reviewed → approved, with
   approval routed through ``custom_approval_engine`` and one-click "Push to
   Project Backlog" creating ``project.task`` records under a dedicated
   "Hub Backlog - BRD" project.

The module degrades gracefully if PyMuPDF / python-docx / python-pptx are
not installed in the runtime: it still loads, but the *Extract* action
shows a user-friendly install hint.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Custom Platform/Operations",
    "version": "19.0.0.5.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_ai_features",
        "custom_ai_bridge",
        "custom_approval_engine",
        "custom_super_admin",
        "project",
        "custom_documents",
        "mail",
        "portal",
        "queue_job",
    ],
    "external_dependencies": {
        "python": ["PyMuPDF", "python-docx", "python-pptx"],
    },
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "data/capability_tag_seed.xml",
        "data/prompts/brd_analysis.xml",
        "data/cron.xml",
        "data/cron_drift_notify.xml",
        "views/module_capability_tag_views.xml",
        "views/module_capability_entry_views.xml",
        "views/brd_document_views.xml",
        "views/brd_lesson_views.xml",
        "views/brd_reject_as_lesson_wizard_views.xml",
        "views/brd_knowledge_regen_wizard_views.xml",
        "views/brd_recommendation_views.xml",
        "views/menu_views.xml",
        "reports/brd_report_templates.xml",
    ],
    "demo": [
        "data/demo_data.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "custom_brd_analyzer/static/src/js/brd_report/brd_report.js",
            "custom_brd_analyzer/static/src/js/brd_report/brd_report.xml",
            "custom_brd_analyzer/static/src/js/brd_report/brd_report.scss",
        ],
    },
    "post_init_hook": "_brd_post_init",
    "installable": True,
    "application": True,
}
