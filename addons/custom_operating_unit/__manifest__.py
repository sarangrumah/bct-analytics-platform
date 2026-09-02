{
    "name": "Operating Unit",
    "summary": "Operating Unit dimension: a hierarchical unit of operation inside a company.",
    "description": """
Operating Unit
==============

Adds the ``operating.unit`` dimension the warehouse groups by, and stamps a stored, indexed
``operating_unit_id`` onto ``sale.order``, ``account.move``, ``stock.picking`` and ``pos.order``.
``ppob.transaction`` carries its own copy of the same field, declared in ``custom_ppob``.

``res.users`` gains ``allowed_operating_unit_ids`` and ``default_operating_unit_id``.
``allowed_operating_unit_ids`` is the field the login-gateway reads to populate the ``allowed_ou``
JWT claim of frozen contract 02.

Record rules restrict each stamped model to the current user's allowed units. See
MODULE_KNOWLEDGE.md for the hierarchy semantics, the company relationship, and the exact
fail-closed behaviour of the rules.
""",
    "version": "19.0.1.0.0",
    "category": "Productivity",
    "author": "BCT Analytics Platform",
    "website": "https://example.invalid/bct",
    "license": "LGPL-3",
    "depends": [
        "base",
        "sale",
        "account",
        "stock",
        "point_of_sale",
    ],
    "data": [
        "security/operating_unit_groups.xml",
        "security/ir.model.access.csv",
        "security/operating_unit_rules.xml",
        "views/operating_unit_views.xml",
        "views/res_users_views.xml",
        "views/inherited_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
    "auto_install": False,
}
