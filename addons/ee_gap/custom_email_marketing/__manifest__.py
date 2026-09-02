# -*- coding: utf-8 -*-
{
    "name": "Custom Email Marketing",
    "summary": "Template gallery, A/B testing, and UU PDP-compliant unsubscribe on top of mass_mailing",
    "description": """
Custom Email Marketing extends standard CE `mass_mailing` with:

- Template gallery (categorised reusable HTML templates with thumbnails) +
  one-click "Apply Template" wizard on the mailing form.
- A/B testing harness (`custom.email.ab.test`) with split-send 50/50,
  scheduled winner evaluation cron, and automatic winner mailing creation.
- UU PDP (Indonesian Personal Data Protection Law) unsubscribe footer
  rendered dynamically with data controller name + DPO contact + the
  standard one-click unsubscribe URL.
- Consent ledger integration: actual filter of recipients by
  pdp.consent.purpose at send time, with audit log of filtered count.
- Open/click tracking enhancement on mailing.trace (first_open_at,
  open_count, click_count) and a 3-bounce auto-blacklist on
  mail.blacklist.
""",
    "author": "Custom Platform",
    "category": "Marketing/Email Marketing",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_pdp_consent",
        "mass_mailing",
        "queue_job",
    ],
    "capability_tags": ["marketing", "pdp", "audit-trail", "ab-testing"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ab_test_cron.xml",
        "views/email_template_gallery_views.xml",
        "views/mailing_mailing_views.xml",
        "views/custom_email_ab_test_views.xml",
        "wizards/custom_email_apply_template_wizard_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
