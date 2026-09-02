# -*- coding: utf-8 -*-
{
    "name": "Custom AI Features",
    "summary": "AI-driven assistance: Ask AI everywhere, anomaly inbox, NLQ chat, document auto-classify",
    "description": """
Custom AI Features
==================

Surfaces the platform's ai-gateway capabilities throughout the Odoo UI:

- **Ask AI** server-action attached to 9 key business models — appears
  under the cog menu of every record on those models. Renders the
  workflow-recommend output in a transient wizard.
- **Anomaly Inbox** — schedulers scan account.move, hr.payslip,
  custom.coretax.transaction nightly and surface high-confidence
  outliers as ``ai.anomaly.finding`` rows with severity + suggested
  action.
- **NLQ Chat** — ``/ai/chat`` portal page lets users ask "tampilkan
  faktur > Rp 100 juta posted bulan ini" and get a structured query
  back, executed live (read-only) with PII masking honoured.
- **Document Auto-Classify** — when a ``document.document`` is created,
  the ai-gateway suggests a ``pdp.classification`` + tag set; admin
  can accept or override.
""",
    "author": "Custom Platform",
    "category": "Productivity/AI",
    "version": "19.0.0.1.1",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_taxonomy",
        "custom_pdp_audit",
        "custom_ai_bridge",
        "custom_approval_engine",
        "custom_coretax_pajakku",
        "custom_documents",
        "custom_field_service",
        "custom_helpdesk",
        "custom_hr_payroll_id",
        "mail",
        "portal",
        "website",
    ],
    "capability_tags": ["ai", "anomaly-detection", "pdp", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "data/ask_ai_actions_data.xml",
        "views/ai_anomaly_finding_views.xml",
        "views/ai_anomaly_scan_views.xml",
        "views/ai_nlq_session_views.xml",
        "views/portal_chat_templates.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
