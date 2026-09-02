# -*- coding: utf-8 -*-
{
    "name": "Custom Recruitment ID",
    "summary": "Indonesia recruitment localization: job-board webhook ingestion + PDP-aware applicant retention + EE-equivalent recruitment features",
    "description": """
Custom Recruitment ID extends Odoo CE hr_recruitment with Indonesia-specific
capabilities and EE-equivalent recruitment features required by the
multi-tenant platform:

- Job-board source tracking (Jobstreet, Glints, LinkedIn, Kalibrr, Direct)
- Webhook intake (HMAC-SHA256 verified) endpoint for Jobstreet / Glints /
  LinkedIn payloads.
- Candidate dedup via SHA1(lower(email) + normalize(phone)) hash with
  duplicate-of pointer.
- One-click interview scheduling helper that opens a pre-filled
  calendar.event for the applicant + job interviewers.
- Indonesia Offer Letter PDF report (jabatan, gaji, estimasi PPh 21,
  masa percobaan, mulai kerja).
- PDP-aware applicant retention: cron anonymizes expired applicants
  (REDACTED-<id>) while preserving stage history + writing a pdp.audit_log
  trail.
- Job posting auto-publish stub for Jobstreet / Glints with external
  post-id tracking.
""",
    "author": "Custom Platform",
    "category": "Human Resources/Recruitment",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_taxonomy",
        "custom_pdp_audit",
        "custom_pdp_retention",
        "hr_recruitment",
        "calendar",
        "mail",
    ],
    "capability_tags": ["recruitment", "webhook-intake", "pdp", "audit-trail", "indonesian-hr"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/recruitment_id_cron.xml",
        "report/offer_letter.xml",
        "views/hr_applicant_views.xml",
        "views/hr_job_views.xml",
        "views/custom_recruitment_webhook_log_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
