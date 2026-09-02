# -*- coding: utf-8 -*-
{
    "name": "Custom Ops Monitor",
    "summary": "Server health + capacity forecast UI for multi-tenant operations",
    "description": """
Custom Ops Monitor
==================

Pulls live tenant metrics from Prometheus, capacity projections from the
``custom-predictor`` service, and Alertmanager webhooks into Odoo so the
ops team has a single pane of glass:

- **Health Dashboard** — OWL component, heatmap tile per tenant, drill
  to per-tenant time-series + embedded Grafana iframe.
- **Tenant Health History** — every 60s a snapshot is upserted per
  tenant: CPU, memory, disk, request rate, error rate, DB size, Redis
  hit rate, backup freshness.
- **Capacity Forecast** — every hour the predictor service is asked for
  30/90/365-day projections per metric, with severity automatically
  computed against capacity thresholds.
- **Incidents** — ``POST /api/ops/alert`` accepts Alertmanager webhook
  v4 payloads, upserts ``custom.ops.incident`` rows, opens an Odoo
  mail.activity assigned to the on-call ops user.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Administration/Operations",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_super_admin",
        "mail",
        "web",
    ],
    "capability_tags": ["multi-tenant", "anomaly-detection", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/cron.xml",
        "views/tenant_health_views.xml",
        "views/capacity_forecast_views.xml",
        "views/ops_incident_views.xml",
        "views/health_dashboard_views.xml",
        "views/menu_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "custom_ops_monitor/static/src/js/health_dashboard/health_dashboard.js",
            "custom_ops_monitor/static/src/js/health_dashboard/health_dashboard.xml",
            "custom_ops_monitor/static/src/js/health_dashboard/health_dashboard.scss",
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
}
