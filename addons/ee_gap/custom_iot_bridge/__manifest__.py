# -*- coding: utf-8 -*-
{
    "name": "Custom IoT Bridge",
    "summary": "Ingest sensor/device readings via webhook; surface in dashboards + threshold alerts",
    "description": """
Lightweight IoT data bridge. iot.device represents a physical device
(sensor, gateway, PLC). iot.reading rows capture timestamped values
ingested via a tokenised webhook endpoint (POST /iot/ingest). Each
device may have one or more iot.threshold rules that auto-create an
alert when triggered.
""",
    "author": "Custom Platform",
    "category": "IoT",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": ["custom_core", "custom_pdp_audit", "mail"],
    "capability_tags": ["iot", "audit-trail", "anomaly-detection"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/iot_device_views.xml",
        "views/iot_reading_views.xml",
        "views/iot_threshold_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
