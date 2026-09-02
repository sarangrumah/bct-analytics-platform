# -*- coding: utf-8 -*-
{
    "name": "Custom WMS Integration",
    "summary": "External WMS / host (SAP and generic) integration: inbound REST, outbound adapter, event outbox",
    "description": """
Two-way integration between Odoo Inventory and an external WMS or host system
(SAP WM/EWM, or any generic REST host).

Inbound (host -> Odoo), all under ``/api/wms/*`` and guarded by the platform
``@secure_endpoint('wms')`` decorator (HMAC-SHA256 + timestamp drift + nonce
replay + CIDR allowlist):

- ``POST /api/wms/asn``   — Advance Ship Notice, idempotent on ``external_ref``
- ``POST /api/wms/do``    — Delivery Order, idempotent on ``external_ref``
- ``GET  /api/wms/stock`` — paginated on-hand by sku / location / warehouse
- ``POST /api/wms/ack``   — host acknowledgement of an event we pushed

Outbound (Odoo -> host) through ``custom_adapter_framework``:

- ``@register_adapter("wms_host")``     — generic REST host
- ``@register_adapter("wms_sap_host")`` — SAP path conventions

Every outbound push is persisted first in the ``wms.integration.event`` outbox
and drained by a cron, so a host outage never rolls back a warehouse
transaction. Host codes that differ from Odoo's are translated by
``wms.integration.mapping``.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Inventory/Warehouse",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_adapter_framework",
        "stock",
        "purchase",
        "sale_management",
    ],
    "capability_tags": ["wms", "integration", "sap", "audit-trail", "multi-tenant"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "data/adapter_config_data.xml",
        "data/cron.xml",
        "views/wms_integration_event_views.xml",
        "views/wms_integration_mapping_views.xml",
        "views/wms_adapter_config_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
