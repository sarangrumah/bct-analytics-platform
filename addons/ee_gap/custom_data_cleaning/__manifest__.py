# -*- coding: utf-8 -*-
{
    "name": "Custom Data Cleaning",
    "summary": "CE-based data cleaning: deduplication rules and Indonesian format normalization (phone, NIK)",
    "description": """
Custom Data Cleaning
====================

A community-edition substitute for the EE-only ``data_cleaning`` module.
Builds on the CE ``data_recycle`` module and adds:

- **Deduplication rules** (``custom.dedup.rule``) — define a target model,
  the match fields, and optional normalization options (Indonesian phone
  canonicalisation to ``+62...``, email case folding) before comparing.
- **Duplicate candidates** (``custom.dedup.candidate``) — records produced
  by a rule scan; reviewers can merge or dismiss.
- **Merge wizard** (``custom.dedup.merge.wizard``) — guided merge with
  conflict-aware preservation of master values.
- **Normalize wizard** (``custom.dedup.normalize.wizard``) — bulk run
  phone/NIK normalization on any model.
- **Recycle presets** — leverages ``data_recycle`` to surface stale
  archived contacts, dormant draft leads, and old cancelled sales.
- **Helpers** — module-level ``_normalize_phone_id`` and ``_validate_nik``
  callables used by other modules (HR, contacts, KYC).

Audit trail flows through ``custom_pdp_audit`` (mail.thread).
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Productivity/Data Cleaning",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "data_recycle",
    ],
    "capability_tags": ["pdp", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/data_recycle_presets.xml",
        "views/custom_dedup_rule_views.xml",
        "views/custom_dedup_candidate_views.xml",
        "views/custom_dedup_merge_wizard_views.xml",
        "views/custom_dedup_normalize_wizard_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
