# -*- coding: utf-8 -*-
{
    "name": "Custom eLearning",
    "summary": "Extend website_slides with Indonesian certificates, cohorts, and appraisal integration",
    "description": """
Custom eLearning extends the CE website_slides module with:
- Learner cohort / batch management (custom.elearning.cohort)
- Certificate (sertifikat) generation in Bahasa Indonesia (qweb-pdf)
- Course catalog filter fields (level, duration, validity, category)
- Quiz scoring with configurable passing threshold
- HR department-based auto-enrolment into cohorts
- Completion reminder cron for mid-point cohorts
- Skills auto-assign on completion (hr.skill bridge when installed)
""",
    "author": "Custom Platform",
    "category": "Websites/eLearning",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "website_slides",
        "custom_hr_appraisal",
        "hr",
        "mail",
    ],
    "capability_tags": ["knowledge", "indonesian-payroll", "crm"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/mail_template_data.xml",
        "data/ir_cron_data.xml",
        "reports/certificate_report.xml",
        "views/custom_elearning_cohort_views.xml",
        "views/slide_channel_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
