{
    "name": "Custom ESG Reporting",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "author": "Custom Platform",
    "category": "Accounting/ESG Reporting",
    "summary": "Environmental, Social, Governance metrics for OJK POJK 51/2017 sustainability reporting",
    "description": """
Custom ESG Reporting
====================

Standalone ESG metric capture and sustainability report generation for Indonesian
SMB / listed companies subject to OJK POJK 51/2017 sustainability reporting
obligations. Supports POJK 51, GRI, SASB, and TCFD frameworks.

Features
--------
* ESG metric catalog (Environmental / Social / Governance) with GRI/POJK 51 codes
* Period-bound measurements with draft / validated / audited workflow
* Sustainability report generator aggregating measurements by category
* Mail.thread tracking on metrics and measurements
* Seeded POJK 51 metric catalog
""",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "account",
        "hr",
    ],
    "capability_tags": ["accounting", "audit-trail", "approval-workflow", "anomaly-detection"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/esg_metrics_pojk51.xml",
        "views/custom_esg_metric_views.xml",
        "views/custom_esg_measurement_views.xml",
        "views/custom_esg_report_views.xml",
        "views/menu_views.xml",
    ],
    "application": True,
    "installable": True,
    "auto_install": False,
}
