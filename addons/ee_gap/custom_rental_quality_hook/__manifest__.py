# -*- coding: utf-8 -*-
{
    "name": "Custom Rental — Quality & Maintenance Hook",
    "summary": "Auto-create quality.check pada rental return; link rental.asset ↔ maintenance.equipment",
    "description": """
Wires three previously-disconnected modules together:

* ``rental.asset.equipment_id`` (M2o → maintenance.equipment) — failure
  history dari maintenance.request kini nempel ke asset rental, terlihat
  langsung di kartu asset.
* ``rental.asset.default_quality_point_id`` — quality template untuk
  inspection saat handover / return.
* On ``rental.order.action_return``, otomatis spawn ``quality.check``
  dari template; jika operator mark fail, ``quality.alert`` (NCR)
  ter-create + opsional auto-spawn ``maintenance.request`` corrective.
* New button on rental.order: "Create Maintenance Request" — quick
  channel saat kerusakan terlihat di field tanpa wajib lewat quality
  check.
""",
    "author": "Custom Platform",
    "category": "Sales/Rental",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_rental",
        "custom_quality_full",
        "custom_maintenance",
    ],
    "capability_tags": ["rental", "quality", "maintenance", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "views/rental_asset_views.xml",
        "views/rental_order_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
