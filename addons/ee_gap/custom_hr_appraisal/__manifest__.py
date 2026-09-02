# -*- coding: utf-8 -*-
{
    "name": "Custom HR Appraisal",
    "summary": "Performance reviews with templates, 360 feedback, and competency scoring",
    "description": """
Performance appraisal workflow. Define review templates with weighted
competency items. Each cycle creates appraisal records for in-scope
employees; managers + employees fill scores + comments; HR closes.
""",
    "author": "Custom Platform",
    "category": "Human Resources/Appraisal",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": ["custom_core", "custom_pdp_audit", "hr", "mail"],
    "capability_tags": ["knowledge", "audit-trail", "pdp", "approval-workflow"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/appraisal_template_views.xml",
        "views/appraisal_cycle_views.xml",
        "views/appraisal_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
