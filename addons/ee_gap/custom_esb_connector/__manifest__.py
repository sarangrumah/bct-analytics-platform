# -*- coding: utf-8 -*-
{
    "name": "Custom ESB Connector",
    "summary": "Integration engine for ESB Core / ESB OMS (esb.co.id F&B ERP): session-managed "
    "REST adapters, master-data mirror, stock snapshot and an idempotent document outbox",
    "description": """
Custom ESB Connector
====================

The Odoo side of the ESB edge. ESB Core remains the **source of truth for stock**;
this module mirrors ESB master data and balances into Odoo and pushes documents
back as native ESB records. It contains no business logic — forecasting, counting
and replenishment live in the consuming vertical module.

- Registers ``esb_core`` / ``esb_corev1`` / ``esb_oms`` adapters on top of
  ``custom_adapter_framework`` (retry, circuit breaker, append-only call log).
  ESB serves three different hosts, hence three ``custom.adapter.config`` rows.
- ``custom.esb.session`` manages the 1-hour JWT access token and 24-hour refresh
  token behind a row lock, because ESB evicts any existing session on login.
  A static API key issued by the ESB PIC is supported and preferred.
- Unwraps the ESB response envelope: ``HTTP 200`` with ``status: "fail"`` is a
  failure, and envelope errors are mapped to non-retryable status codes.
- Mirrors branches, locations, units, purposes, document templates, products and
  product details, keyed by ESB IDs for idempotent upsert.
- ``custom.esb.stock.snapshot`` derives on-hand balances from the stock-movement
  report, because ESB exposes no bulk stock-on-hand endpoint.
- ``custom.esb.outbox`` is the single outbound path (item journal, purchase
  request, goods transfer request, purchase order), pushed asynchronously via
  ``queue_job``. ESB has no idempotency header, so the outbox stamps a key into
  ``additionalInfo`` and checks the Index endpoint before creating.

Everything degrades gracefully: with no active ``custom.adapter.config`` the
crons no-op and pushes stay queued, so the module is installable long before ESB
credentials exist. All outbound writes are additionally gated behind the
``esb.push_enabled`` system parameter, which defaults to off.

See ``docs/integrations/esb-core-api.md`` for the API reference.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Inventory/Inventory",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_adapter_framework",
        "custom_pdp_audit",
        "queue_job",
        "stock",
        "uom",
    ],
    "capability_tags": ["esb-integration", "fnb", "master-sync", "stock-mirror", "outbox"],
    "data": [
        "views/generated_search_views.xml",
        "security/esb_security.xml",
        "security/ir.model.access.csv",
        "data/adapter_config_data.xml",
        "data/queue_job_function_data.xml",
        "data/ir_cron_data.xml",
        "views/esb_config_views.xml",
        "views/esb_master_views.xml",
        "views/esb_outbox_views.xml",
        "views/esb_menus.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
