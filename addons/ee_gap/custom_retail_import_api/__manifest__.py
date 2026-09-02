# -*- coding: utf-8 -*-
{
    "name": "Retail Import — MDM Product API",
    "summary": "Inbound REST API for near-realtime product-master feeds (the apparel brand MDM HUB)",
    "description": """
Retail Import — MDM Product API
===============================

Receives product-master JSON pushed by an upstream MDM hub instead of pulling the
same data from a scheduled report. Built for the apparel brand, whose XStore MDM HUB feeds
Odoo through SAP PO -> IBM MQ -> Mulesoft; running the SSRS X101 report often
enough to stay current was loading their XCenter database.

Why a separate module from ``custom_retail_import``
---------------------------------------------------
``ir.http.routing_map`` is built per database from the modules installed in it.
``custom_retail_import`` is installed in several the apparel brand databases; putting the
controller there would expose ``/api/mdm/*`` in all of them. Installing this module
only where the integration is wanted makes the route *not exist* everywhere else --
a stronger guarantee than any runtime flag.

Pieces
------
* ``retail.mdm.request`` / ``retail.mdm.item`` — inbound staging. The raw payload is
  kept, so a mapping bug is fixed by editing code and replaying, never by asking the
  sender to retransmit. A unique ``dedupe_key`` makes a re-POST a no-op.
* ``retail.mdm.category.map`` — crosswalk from the feed's 2-level taxonomy to the
  existing product categories, because ``categ_id`` drives revenue and COGS accounts
  and must not be guessed.
* ``controllers/mdm_api.py`` — six routes behind ``@secure_endpoint('mdm')``.
* The actual product writes go through ``retail.import.executor._x101_upsert_items``,
  the same seam the X101 file import uses, so both routes produce identical records.

Everything is off until switched on: the controller answers 503 unless
``retail_import.mdm_api_enabled`` is 1, and a shadow mode
(``retail_import.mdm_dry_run``) validates real traffic without touching master data.

Request auditing lives on ``retail.mdm.request`` (payload, source IP, timings, state,
per-item outcome) rather than in ``custom.adapter.call.log``: that table is keyed to a
``custom.adapter.config``, whose ``adapter_type`` must name a registered *outbound*
adapter class, and it stores only ``sha256(body)`` -- which is precisely the thing an
inbound feed needs to keep. Rejected requests (bad key, wrong IP, oversize body) are
logged by ``secure_endpoint`` itself.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Inventory/Retail",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_retail_import",
        "queue_job",
        "product",
        "stock",
    ],
    "capability_tags": ["integration", "rest-api", "retail", "product-master", "mdm"],
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "data/queue_job_function_data.xml",
        "views/retail_mdm_request_views.xml",
        "views/retail_mdm_category_map_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
