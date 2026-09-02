# -*- coding: utf-8 -*-
{
    "name": "Custom WhatsApp",
    "summary": "Meta WhatsApp Cloud API adapter with template management and PDP-gated outbound queue",
    "description": """
Custom WhatsApp
===============

Adapter for the Meta WhatsApp Cloud API targeted at Indonesian SMB
tenants where WhatsApp is the dominant messaging channel.

What this module provides
-------------------------
- **Account configuration** (``whatsapp.account``): per-company Meta
  Cloud (or Twilio) credentials with sandbox/production toggle and
  webhook verify token.
- **Message templates** (``whatsapp.template``): named templates with
  language, category (marketing / utility / authentication), body text
  and computed variable count (``{{n}}`` placeholders). Tracks Meta
  approval status (draft / pending_review / approved / rejected) and
  the upstream ``meta_template_id``.
- **Outbound message queue** (``whatsapp.message``): persisted send
  records with delivery lifecycle (draft -> queued -> sent -> delivered
  -> read / failed). Audited via ``pdp.audited.mixin``. Inbound
  messages from webhook callbacks are stored with direction='inbound'.
- **PDP consent gating**: marketing-category sends require an active
  consent record for the target partner under purpose code
  ``whatsapp_marketing``; utility sends check
  ``whatsapp_utility``. Marketing sends without consent raise
  ``UserError``.
- **Webhook controller** for Meta Cloud API status callbacks and
  inbound messages (verify token + signature check).
- **Integration buttons** on ``sale.order``, ``account.move`` and
  ``helpdesk.ticket`` (when ``custom_helpdesk`` is installed) via a
  ``whatsapp.send.wizard`` TransientModel.
- **Async dispatch** through ``queue_job`` (channel ``root.whatsapp``)
  for bulk sends > 5 records.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Productivity/WhatsApp",
    "version": "19.0.0.4.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_ai_bridge",
        "custom_pdp_audit",
        "custom_pdp_consent",
        "custom_pdp_taxonomy",
        "mail",
        "queue_job",
        "sale_management",
        "account",
        "custom_helpdesk",
    ],
    "capability_tags": ["whatsapp", "marketing", "pdp", "audit-trail", "crm", "helpdesk"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/whatsapp_cron.xml",
        "data/queue_job_channel.xml",
        "views/whatsapp_account_views.xml",
        "views/whatsapp_template_views.xml",
        "views/whatsapp_message_views.xml",
        "views/whatsapp_send_wizard_views.xml",
        "views/sale_order_views.xml",
        "views/account_move_views.xml",
        "views/helpdesk_ticket_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
