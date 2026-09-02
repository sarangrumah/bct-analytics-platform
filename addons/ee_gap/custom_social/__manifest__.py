# -*- coding: utf-8 -*-
{
    "name": "Custom Social",
    "summary": "Social media account + scheduled post management (Facebook/IG/X stubs)",
    "description": """
Manage multiple social media accounts + schedule outbound posts.
Posts move through draft → scheduled → published. Provider adapter
abstraction lets each platform (FB/IG/X/LinkedIn) plug in a publish
implementation. Default 'manual' adapter logs but doesn't push.
""",
    "author": "Custom Platform",
    "category": "Marketing/Social",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": ["custom_core", "custom_pdp_audit", "mail"],
    "capability_tags": ["marketing", "social-media", "pdp", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "views/social_account_views.xml",
        "views/social_post_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
