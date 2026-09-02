{
    "name": "PPOB - Payment Point Online Bank",
    "summary": "Bill payment and top-up transactions, with billers, an SLA clock and a state machine.",
    "description": """
PPOB
====

A transaction vertical: one ``ppob.transaction`` is one bill payment or top-up forwarded to a
``ppob.biller``.

Why it exists in this repository: master prompt section 3.1 names a PPOB fact table, and a
dimensional model cannot be tested against a source table that does not exist. This module is that
source table.

What the warehouse depends on:

* ``state`` moves only along the five legal transitions - dbt asserts ``accepted_values`` on it.
* ``sla_seconds`` is a stored compute, so it is a real Postgres column that logical decoding sees.
* ``operating_unit_id`` is stored and indexed, exactly as on the four stock models.
* ``customer_ref`` is classified ``sensitive`` in ``custom_pdp_core``, hashed before it reaches the
  warehouse, and masked in the Odoo UI for users outside PDP / Data Viewer.
""",
    "version": "19.0.1.0.0",
    "category": "Sales",
    "author": "BCT Analytics Platform",
    "website": "https://example.invalid/bct",
    "license": "LGPL-3",
    "depends": [
        "base",
        "product",
        "custom_pdp_core",
        "custom_pdp_masking",
        "custom_operating_unit",
    ],
    "data": [
        "security/ppob_groups.xml",
        "security/ir.model.access.csv",
        "security/ppob_rules.xml",
        "data/ir_sequence_data.xml",
        "views/ppob_biller_views.xml",
        "views/ppob_transaction_views.xml",
        "views/ppob_menus.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
