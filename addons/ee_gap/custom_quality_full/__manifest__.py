# -*- coding: utf-8 -*-
{
    "name": "Custom Quality (Full)",
    "summary": "Quality points + checks + NCR alerts + inspection lines + signatures + CAPA + reusable test templates",
    "description": """
Extended quality module: in addition to the baseline quality.point /
quality.check / quality.alert (NCR) skeleton, this version adds:

* Multi-line inspection lines per check (with min/max ranges and accepted
  value sets).
* Tamper-evident e-signatures on checks and CAPAs.
* Structured CAPA records linked to alerts (corrective / preventive /
  containment), with auto-resolve cascade.
* Reusable Quality Test templates that can be applied to a check or
  attached as a default to a quality.point.
""",
    "author": "Custom Platform",
    "category": "Manufacturing/Quality",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": ["custom_core", "custom_pdp_audit", "mrp", "stock", "mail"],
    "capability_tags": ["quality", "manufacturing", "audit-trail", "approval-workflow"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "views/quality_test_views.xml",
        "views/quality_point_views.xml",
        "views/quality_check_views.xml",
        "views/quality_alert_views.xml",
        "views/quality_capa_views.xml",
        "views/quality_signature_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
