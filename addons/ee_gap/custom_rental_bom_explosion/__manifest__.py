# -*- coding: utf-8 -*-
{
    "name": "Custom Rental — BOM Explosion",
    "summary": "Bundling drone + perangkat via BOM kit, otomatis populate BAST lines saat pickup/return rental",
    "description": """
Extends ``custom_rental`` so that a rental.asset (or its product.product)
with a ``mrp.bom`` of type ``phantom`` (kit) gets its components
auto-exploded into the BAST document lines on pickup / return.

Use case: PT rental sewa drone bundle (body + kamera + 2 battery +
charger + controller). Operator menjual SATU sku rental, tapi BAST
listing detail komponennya untuk handover & condition tracking.

Without this module, BAST lines must be entered manually.
""",
    "author": "Custom Platform",
    "category": "Sales/Rental",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_rental",
        "custom_bast",
        "mrp",
    ],
    "capability_tags": ["rental", "bom", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "views/rental_asset_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
