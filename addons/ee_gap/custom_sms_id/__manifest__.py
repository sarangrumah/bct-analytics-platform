# -*- coding: utf-8 -*-
{
    "name": "Custom SMS Indonesia",
    "summary": "SMS adapter for Indonesian (Zenziva) and global (Twilio) providers with PDP-gated marketing",
    "description": """
Custom SMS Indonesia
====================

Multi-provider SMS adapter targeted at Indonesian SMB tenants.

What this module provides
-------------------------
- **Account configuration** (``custom.sms.account``): per-company
  credentials for Zenziva (local) and Twilio (global), with sandbox /
  production toggle and per-provider field hints.
- **Outbound message queue** (``custom.sms.message``): persisted send
  records with delivery lifecycle (draft -> queued -> sent ->
  delivered -> failed). Audited via ``pdp.audited.mixin``.
- **Purpose tagging**: OTP, transactional, or marketing.
- **PDP consent gating**: marketing sends require an active consent
  record for the target partner under purpose code ``sms_marketing``
  or they raise ``UserError``. OTP / transactional sends log a warning
  when consent is missing but do not block.
- **Adapter pattern**: pluggable base + zenziva / twilio subclasses;
  the actual HTTP send is stubbed (``_logger.info``) until live
  credentials are wired in.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Marketing/SMS",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_pdp_consent",
        "mail",
        "sms",
    ],
    "capability_tags": ["marketing", "pdp", "audit-trail", "crm"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/custom_sms_account_views.xml",
        "views/custom_sms_message_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
