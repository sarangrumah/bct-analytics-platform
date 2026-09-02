# -*- coding: utf-8 -*-
{
    "name": "Custom CRM",
    "summary": "CRM EE-equivalent: lead mining, predictive scoring, enrichment, web-form intake, automation",
    "description": """
Custom CRM extends the standard Odoo `crm.lead` model with EE-equivalent
features built on top of CE:

- AI-driven lead scoring via custom_ai_bridge (rules + AI bridge)
- Rules-based predictive lead scoring (x_predictive_score 0-100)
- Lead enrichment stub via custom.ai._recommend
- Lead mining request stub (mock IAP credits, generate draft leads)
- Web-form / webhook intake tokens for external lead capture
- Sample base.automation rules: round-robin assign, follow-up activity
- WhatsApp contact channel for Indonesian SMB outreach
- PDP audit logging on lead owner (salesperson) changes
""",
    "author": "Custom Platform",
    "category": "Sales/CRM",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_taxonomy",
        "custom_pdp_audit",
        "custom_ai_bridge",
        "crm",
        "mail",
        "base_automation",
    ],
    "capability_tags": ["crm", "ai", "pdp", "audit-trail", "whatsapp"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/crm_lead_views.xml",
        "views/custom_crm_lead_mining_request_views.xml",
        "views/custom_crm_web_form_token_views.xml",
        "views/menu_views.xml",
        "data/crm_automation_rules.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
