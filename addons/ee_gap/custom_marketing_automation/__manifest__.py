# -*- coding: utf-8 -*-
{
    "name": "Custom Marketing Automation",
    "summary": "Multi-step email campaigns + drip sequences + audience segmentation",
    "description": """
Lightweight marketing automation. Define audience segments via a domain
on res.partner, build multi-step campaigns (email → wait → email →
condition → email), and let a scheduler tick through participant flows.
Respects custom_pdp_consent (no email without valid 'marketing' consent).
""",
    "author": "Custom Platform",
    "category": "Marketing/Marketing Automation",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": ["custom_core", "custom_pdp_audit", "custom_pdp_consent", "mail"],
    "capability_tags": ["marketing", "pdp", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "views/marketing_segment_views.xml",
        "views/marketing_campaign_views.xml",
        "views/marketing_participant_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
