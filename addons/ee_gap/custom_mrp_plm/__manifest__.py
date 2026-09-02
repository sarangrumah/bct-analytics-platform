# -*- coding: utf-8 -*-
{
    "name": "Custom MRP PLM",
    "summary": "Product Lifecycle Management: ECO workflow, BoM versioning, approval-gated changes",
    "description": """
PLM layer on top of Odoo MRP. Engineering Change Orders (ECO) capture
proposed changes to a product or BoM; reviewers approve in tiers; on
final approval, the new revision is promoted to active. Old BoMs are
archived (not deleted) for audit + traceability.
""",
    "author": "Custom Platform",
    "category": "Manufacturing/PLM",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": ["custom_core", "custom_pdp_audit", "mrp", "mail"],
    "capability_tags": ["plm", "manufacturing", "approval-workflow", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "views/mrp_eco_stage_views.xml",
        "views/mrp_eco_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
