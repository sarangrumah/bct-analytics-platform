# -*- coding: utf-8 -*-
{
    "name": "Custom Indonesia Payment Gateway",
    "summary": "Midtrans / Xendit / DOKU adapters for payment.provider",
    "description": """
Indonesia payment gateway adapter for Odoo 19 payment.* stack.

Phase 3 (Tier 3 EE-gap):

- Extends payment.provider with Midtrans/Xendit/DOKU as additional codes
  and provider-specific configuration fields (server_key, client_key,
  merchant_id, sandbox toggle, webhook_secret).
- HTTP adapter base class with retry / exponential backoff / circuit
  breaker (pattern lifted from custom_coretax_pajakku).
- Concrete adapter stubs (MidtransAdapter, XenditAdapter, DokuAdapter)
  that currently log payloads only — live API plumbing is wired in a
  follow-up once sandbox credentials are provisioned per tenant.
- Outbound call log (custom.payment.id.log) with mail.thread tracking
  on state for ops visibility and PDP audit.
""",
    "author": "Custom Platform",
    "category": "Accounting/Payment Providers",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "payment",
        "custom_subscription",
    ],
    "capability_tags": ["payment-acquirer", "indonesia-gateway", "webhook", "audit-trail"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/payment_provider_views.xml",
        "views/custom_payment_id_log_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
