# -*- coding: utf-8 -*-
{
    "name": "Custom Onboarding Journey",
    "summary": "Lifecycle orchestrator: intake -> BRD -> Go/No-Go -> provisioning -> handover, with bi-directional Project sync",
    "description": """
Custom Onboarding Journey
=========================

Coordinates the entire tenant onboarding lifecycle as a single state machine
(``onboarding.journey``). Each stage transition is appended to an immutable
audit log (``onboarding.stage.transition``).

Bi-directional sync with ``project.project``: each journey owns a project
seeded from a template; stage changes propagate to kanban columns, and
movements of "stage marker" tasks back-propagate to the journey stage. Loop
prevention is handled via a ``_skip_journey_sync`` context flag, and
conflicts use last-write-wins on a per-record ``sync_version`` counter.

A public intake controller (``/onboarding/public/intake``) lets prospects
submit company profile + wishlist via the marketing site, optionally guarded
by Cloudflare Turnstile and a per-IP rate limit.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Custom Platform/Operations",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_brd_analyzer",
        "custom_super_admin",
        "custom_approval_engine",
        "custom_tenant_infra",
        "project",
        "mail",
        "portal",
    ],
    "capability_tags": ["multi-tenant", "approval-workflow", "audit-trail", "crm"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/onboarding_journey_views.xml",
        "views/onboarding_stage_transition_views.xml",
        "views/onboarding_public_submission_views.xml",
        "views/brd_document_inherit_views.xml",
        "views/brd_recommendation_inherit_views.xml",
        "wizards/intake_wizard_views.xml",
        "wizards/brd_upload_wizard_views.xml",
        "wizards/go_no_go_wizard_views.xml",
        "views/menu_views.xml",
    ],
    "demo": [
        "data/demo_data.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
    "external_dependencies": {"python": ["requests"]},
}
