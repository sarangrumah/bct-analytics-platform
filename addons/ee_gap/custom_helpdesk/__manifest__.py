# -*- coding: utf-8 -*-
{
    "name": "Custom Helpdesk",
    "summary": "Ticket pipeline with SLA, escalation and customer portal",
    "description": """
Custom Helpdesk is a CE-targeted reimplementation of capabilities documented at
https://www.odoo.com/documentation/19.0/applications/services/helpdesk.html.

Phase 2E MVP:
- Teams with mail aliases (email-to-ticket)
- Tickets with priority/state, chatter, tags
- SLA policies with deadline + status computation (cron)
- AI Suggested Response via custom_ai_bridge
- PDP audit logging on state transitions
""",
    "author": "Custom Platform",
    "category": "Services/Helpdesk",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_ai_bridge",
        "custom_pdp_audit",
        "mail",
        "project",
    ],
    "capability_tags": ["helpdesk", "ai", "audit-trail", "pdp"],
    "data": [
        "views/generated_search_views.xml",
        "security/custom_helpdesk_security.xml",
        "security/ir.model.access.csv",
        "data/helpdesk_sequence.xml",
        "data/helpdesk_cron.xml",
        "views/helpdesk_sla_views.xml",
        "views/helpdesk_tag_views.xml",
        "views/helpdesk_team_views.xml",
        "views/helpdesk_ticket_views.xml",
        "views/menu_views.xml",
        "data/helpdesk_sample_data.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
