# -*- coding: utf-8 -*-
{
    "name": "Custom Field Service",
    "summary": "Technician dispatch, on-site work orders, materials, signature capture",
    "description": """
Lightweight field-service module. Work orders linked to a customer
+ site, assigned to a technician, tracked through scheduled → in
progress → completed with materials consumed and a captured customer
signature. Audited via pdp.audit_log.
""",
    "author": "Custom Platform",
    "category": "Services/Field Service",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_taxonomy",
        "custom_pdp_audit",
        "mail",
        "stock",
        "product",
    ],
    "capability_tags": ["field-service", "audit-trail", "pdp"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "views/fsm_site_views.xml",
        "views/fsm_skill_views.xml",
        "views/fsm_technician_views.xml",
        "views/fsm_work_order_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
