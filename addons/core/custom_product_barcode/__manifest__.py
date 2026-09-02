# -*- coding: utf-8 -*-
{
    "name": "Custom Product Barcode (Multi-barcode)",
    "summary": "Alternate barcodes per product variant — one variant, one inventory, all scannable",
    "description": """
Lets a product variant carry MORE THAN ONE barcode. The native
``product.product.barcode`` stays the primary (e.g. the latest GTIN); additional
GTINs are stored as ``product.barcode`` rows and matched by
``product.product._resolve_barcode`` (primary first, then alternates).

Inventory is unchanged — still tracked per variant — because the extra GTINs are
alternate barcodes of the SAME stock unit (e.g. historical/re-issued GTINs or
packaging-level codes), not separate items.

Kept dependency-light (``product`` only) so it can be installed on any tenant that
imports multi-GTIN master data, without pulling in the full barcode-scanning app.
Scanning modules (``custom_barcode``, ``custom_hht_bridge``) depend on this and use
the resolver so alternate barcodes scan to the same product.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Inventory",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": ["product"],
    "capability_tags": ["retail", "barcode"],
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "views/product_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
