# -*- coding: utf-8 -*-
{
    "name": "Custom Dev Cycle Tracking",
    "summary": "Full dev lifecycle tracking with GitHub/GitLab PR + CI webhook integration",
    "description": """
Custom Dev Cycle
================

Track each BRD recommendation through its full implementation lifecycle:

* ``dev.cycle`` — backlog → in_dev → code_review → qa → uat → deployed → done
  state machine, linked to a BRD recommendation, an onboarding journey and the
  target hub module.
* ``dev.cycle.pr`` — GitHub / GitLab pull requests with CI status, auto-synced
  via HMAC-validated webhook controllers.
* ``dev.cycle.deployment`` — link to ``custom.hub.module.deployment`` per
  environment.

Webhooks:

* ``POST /devcycle/webhook/github`` — validates ``X-Hub-Signature-256`` HMAC.
* ``POST /devcycle/webhook/gitlab`` — validates ``X-Gitlab-Token``.

When a PR is merged with a successful CI status the linked dev cycle is
auto-transitioned to ``deployed``.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Custom Platform/Operations",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "project",
        "mail",
        "custom_brd_analyzer",
        "custom_onboarding_journey",
    ],
    "capability_tags": ["audit-trail", "approval-workflow", "multi-tenant"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/dev_cycle_views.xml",
        "views/dev_cycle_pr_views.xml",
        "views/dev_cycle_deployment_views.xml",
        "views/brd_recommendation_views.xml",
        "views/menu_views.xml",
    ],
    "demo": [
        "data/demo_data.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
