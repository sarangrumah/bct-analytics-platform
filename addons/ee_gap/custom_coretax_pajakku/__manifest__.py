# -*- coding: utf-8 -*-
{
    "name": "Custom Coretax — Pajakku ASPP Adapter",
    "summary": "Host-to-host adapter for Pajakku (mitrapajakku) as an Authorized Service Provider Pajak",
    "description": """
Custom Coretax Pajakku Adapter
==============================

Concrete implementation of ``custom.coretax.adapter.base`` for Pajakku
(mitrapajakku) — the ASPP this platform's verticals already subscribe
to. When enabled per tenant, e-Faktur and Bukti Potong submissions
flow automatically through Pajakku's API instead of requiring manual
XML upload via the DJP Coretax portal.

What this module provides
-------------------------
- **PajakkuAdapter** (``custom.coretax.adapter.pajakku``) implementing
  ``submit_xml``, ``query_nsfp``, ``download_response`` per the abstract
  base contract.
- **OAuth2 token cache** (in-memory + Redis-backed fallback) with
  automatic refresh.
- **HTTP retry policy**: exponential backoff (1s → 2s → 4s, 3 attempts),
  rate-limit (HTTP 429) honoured via ``Retry-After`` header.
- **Circuit breaker**: 10 consecutive failures → adapter disabled for
  1 hour with an ops alert posted to the company's `mail.thread`.
- **Transaction ledger** (``custom.coretax.transaction``) — every
  outbound submission is persisted with payload, response, retry count,
  state. Audit-logged via ``pdp.audited.mixin``.
- **Usage meter** (``custom.coretax.pajakku.usage``) — per-tenant
  per-month API call + faktur / bupot submission counts for billing.
- **Sync cron** (every 30 min) — polls pending submissions, fetches
  NSFP on approval, stamps the source ``account.move``, raises on
  rejection with operator notification.
- **Per-tenant config** under ``custom.coretax.config``:
  ``pajakku_enabled``, ``pajakku_api_url`` (sandbox/production),
  ``pajakku_client_id`` + ``pajakku_client_secret`` (encrypted via
  ``custom.ir.config``), ``pajakku_sandbox_mode``.
- **"Test Connection" button** on the config form that performs an
  OAuth2 token exchange and reports success / failure.

What this module does NOT do
----------------------------
- No bundled mock server. Adapter is wired to the real Pajakku API.
  Until valid Pajakku sandbox credentials are configured, the adapter
  raises ``UserError`` on submit explaining how to obtain access.
- No alternative ASPP (OnlinePajak, Klikpajak, Pajak.io) — those are
  separate adapter modules sharing the same base.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Accounting/Localizations",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_taxonomy",
        "custom_pdp_audit",
        "custom_coretax",
    ],
    "capability_tags": ["indonesian-tax", "coretax", "audit-trail", "multi-tenant", "withholding"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "views/coretax_config_views.xml",
        "views/coretax_transaction_views.xml",
        "views/coretax_pajakku_usage_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
