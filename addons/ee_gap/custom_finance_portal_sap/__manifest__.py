# -*- coding: utf-8 -*-
{
    "name": "Custom Finance Portal — SAP/HRIS Integration",
    "summary": "Bridge adapter, async push, master-data sync, status webhook and sync log connecting the Finance Portal to SAP & HRIS via the Kafka bridge microservice",
    "description": """
Custom Finance Portal — SAP/HRIS Integration
============================================

The Odoo side of the SAP/HRIS edge. Odoo never talks Kafka directly; it speaks
HMAC-signed REST to a **bridge microservice** that consumes/produces Kafka. This
module:

- Registers ``finance_sap_bridge`` / ``finance_hris_bridge`` adapters on top of
  ``custom_adapter_framework`` (circuit breaker, HMAC signing, retry, call log).
- Overrides ``finance.document.mixin._finance_push_to_sap`` to enqueue an async
  ``queue_job`` that pushes the approved document to SAP (CA GL posting, journal
  posting, reimbursement GL posting, invoice-for-MIRO).
- Pulls master data on a daily scheduler (COA, cost budget, supplier, item
  category, division/vertical, ...) with idempotent upsert by ``x_sap_external_id``.
- Exposes an inbound ``secure_endpoint('finance_sap')`` webhook so the bridge can
  mirror the planned-payment date and payment status back in realtime.
- Logs every push/pull in ``finance.sync.log`` and drives a Sync menu.

Everything is **contract-first** and degrades gracefully: with no enabled
``custom.adapter.config`` the push falls back to the local stub and the crons
no-op, so the portal is usable before the SAP/Kafka connectors are ready.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Accounting/Finance",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_adapter_framework",
        "custom_finance_portal",
        "custom_finance_budget",
        "queue_job",
    ],
    "capability_tags": ["finance-portal", "sap-integration", "kafka-bridge", "master-sync"],
    "data": [
        "security/ir.model.access.csv",
        "data/adapter_config_data.xml",
        "data/ir_cron_data.xml",
        "views/finance_sync_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
