# -*- coding: utf-8 -*-
{
    "name": "Custom WMS Putaway",
    "summary": "Configurable multi-tier putaway engine (generic ZWME001-style)",
    "description": """
Generic configurable putaway strategy engine for WMS.

Closes the CE gap for SAP-style tiered putaway logic (e.g. ZWME001).
Strategies are tier-prioritised and pluggable: fixed_location,
nearest_empty, zone_round_robin, by_volume, by_temperature,
by_abc_velocity, and safe-evaluated custom_python expressions.

On incoming picking validation, proposals are auto-generated; high
confidence proposals (>90) auto-apply, lower ones are surfaced for
operator review at the HHT.

Generic by design — no warehouse-vertical assumptions.
""",
    "author": "Custom Platform",
    "category": "Inventory/Warehouse",
    "version": "19.0.0.3.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_barcode",
        "stock",
        "product",
    ],
    "capability_tags": ["wms", "barcode-scan", "hht", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "data/ir_config_parameter_data.xml",
        "views/putaway_strategy_views.xml",
        "views/putaway_rule_views.xml",
        "views/putaway_proposal_views.xml",
        "views/stock_location_views.xml",
        "views/product_template_views.xml",
        "wizard/putaway_propose_wizard_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
