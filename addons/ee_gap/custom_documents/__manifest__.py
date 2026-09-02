# -*- coding: utf-8 -*-
{
    "name": "Custom Documents",
    "summary": "Document management with workspaces, tagging, versioning, and PDP-aware access",
    "description": """
Workspace-organised document store. Versioning per file, tagging,
share-link generation (token-protected), and PDP audit + classification.
""",
    "author": "Custom Platform",
    "category": "Productivity/Documents",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": ["custom_core", "custom_pdp_audit", "custom_pdp_taxonomy", "mail", "portal"],
    "capability_tags": ["pdp", "audit-trail", "knowledge"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/document_workspace_views.xml",
        "views/document_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
