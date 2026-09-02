# -*- coding: utf-8 -*-
{
    "name": "Custom Survey",
    "summary": "Employee pulse, customer NPS, certification, and appraisal-linked surveys",
    "description": """
Custom Survey extends the CE ``survey`` app with multi-tenant SMB-oriented features:

- Survey kind classification (employee pulse / customer NPS / training feedback / exit interview)
- Certification flow with passing score, HTML certificate template, and validity tracking
- Weighted question scoring and aggregated user-input weighted score
- NPS scoring summaries with promoter/passive/detractor buckets, rolling NPS score, and CSV export
- Anonymity controls (fully anonymous / partial / identified) with PII stripping on answer create
- Optional link from a survey to an ``appraisal.appraisal`` record (when ``custom_hr_appraisal`` is present)
- Seed templates: Employee Engagement Pulse, Customer NPS, Exit Interview

Phase: tier-3 EE gap module. Closes the EE Survey gap with certification + scoring + anonymity primitives.
""",
    "author": "Custom Platform",
    "category": "Marketing/Surveys",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "survey",
        "custom_hr_appraisal",
        "mail",
    ],
    "capability_tags": ["survey", "nps", "certification", "pdp", "audit-trail"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/survey_survey_views.xml",
        "views/survey_question_views.xml",
        "views/custom_survey_nps_summary_views.xml",
        "views/menu_views.xml",
        "data/survey_templates.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
