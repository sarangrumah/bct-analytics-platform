# -*- coding: utf-8 -*-
{
    "name": "Custom Forum",
    "summary": "Website Forum extensions: AI moderation, gamification, PDP author masking",
    "description": """
Custom Forum extends the CE ``website_forum`` module with:

- AI toxicity moderation via ``custom_ai_bridge`` (auto-flag/close offensive posts)
- Badge gamification hooks aimed at Indonesian community members
- PDP-aware author display masking for privacy-sensitive threads
- Hourly cron that batches moderation for unscored active posts
""",
    "author": "Custom Platform",
    "category": "Websites/Forum",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_ai_bridge",
        "website_forum",
    ],
    "capability_tags": ["knowledge", "ai", "moderation", "pdp", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/custom_forum_security.xml",
        "security/ir.model.access.csv",
        "data/forum_moderation_cron.xml",
        "views/forum_post_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
