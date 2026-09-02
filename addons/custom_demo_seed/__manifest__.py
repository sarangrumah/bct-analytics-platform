{
    "name": "Demo Seed (FIXTURE - never install in production)",
    "summary": "Generates 12 months of reproducible, idempotent, obviously-synthetic demo volume.",
    "description": """
Demo Seed
=========

**THIS IS A FIXTURE MODULE. IT MUST NEVER BE PART OF A PRODUCTION INSTALL SET.**

It exists because the Phase 4 performance budget - "p95 under 2 s with 12 months of data" - cannot
be measured against an empty database.

Safeguards, in order of how much they actually protect you:

1. It generates NOTHING at install time. Data appears only when
   ``demo.seed.generator.generate()`` is called explicitly.
2. It is not ``auto_install`` and no other module depends on it, so it is never pulled in.
3. ``generate()`` refuses to run for a non-administrator.
4. Every record it writes is obviously synthetic: ``(Demo NNN)`` name suffixes,
   ``@contoh.invalid`` addresses (RFC 2606 reserved), ``+62-800-555-`` phone numbers,
   ``DEMO-`` prefixed references. ``res.partner.vat`` is left empty on purpose - see
   MODULE_KNOWLEDGE.md.
5. It is idempotent: every record carries an ``ir.model.data`` external ID, so a second run
   creates nothing and uninstalling removes exactly the demo rows.

None of that makes it safe to install in production. Do not add it to the production install set.
""",
    "version": "19.0.1.0.0",
    "category": "Tools",
    "author": "BCT Analytics Platform",
    "website": "https://example.invalid/bct",
    "license": "LGPL-3",
    "depends": [
        "custom_ppob",
        "custom_operating_unit",
        "custom_pdp_core",
        "custom_pdp_masking",
        "sale_management",
        "sale_stock",
        "account",
        "stock",
        "point_of_sale",
        "product",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/demo_seed_views.xml",
    ],
    "installable": True,
    "application": False,
    # Explicitly false. A fixture module must never be dragged in by a dependency.
    "auto_install": False,
}
