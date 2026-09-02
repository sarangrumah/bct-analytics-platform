# -*- coding: utf-8 -*-
{
    "name": "Custom Knowledge",
    "summary": "Lightweight internal wiki / knowledge base",
    "description": """
Custom Knowledge is a CE-targeted reimplementation of Odoo Knowledge
(https://www.odoo.com/documentation/19.0/applications/productivity/knowledge.html).

Features:
- Hierarchical articles (parent/child) with rich text body
- Tags, owners, color, publishing flag
- Per-article read-access restriction via res.groups + ir.rule resolution
- Full-text search via PostgreSQL GIN(to_tsvector)
- Portal share-by-token endpoint /knowledge/share/<token>
- Reusable article templates (Meeting Notes, SOP, Project Brief, Runbook, Onboarding)
- Favorite / pinning per user with "My Favorites" filter
- Version history with one-click restore
- Dynamic Properties bag (definition_record_field=parent_id)
- Chatter, activities and PDP audit on tracked fields
""",
    "author": "Custom Platform",
    "category": "Productivity/Knowledge",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_documents",
        "mail",
        "portal",
    ],
    "capability_tags": ["knowledge", "audit-trail", "pdp"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "security/record_rules.xml",
        "data/knowledge_article_template_seed.xml",
        "views/knowledge_tag_views.xml",
        "views/knowledge_article_views.xml",
        "views/knowledge_article_template_views.xml",
        "views/knowledge_article_version_views.xml",
        "views/knowledge_portal_templates.xml",
        "views/menu_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": True,
    "auto_install": False,
}
