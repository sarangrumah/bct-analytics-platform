# -*- coding: utf-8 -*-
{
    "name": "Custom Sale — BAST Bridge",
    "summary": "Generate and access BAST (handover) documents directly from a Sales Order",
    "description": """
Bridge between standard Sales and ``custom_bast``.

* Adds a **BAST** smart button on the Sales Order form showing the count of
  linked handover documents (matched via the BAST ``reference`` field).
* Adds a **Generate BAST** header button that creates a ``delivery`` BAST
  pre-filled with the company as the handing-over party, the customer as the
  receiving party, ``reference`` pointing back to the order, and one BAST line
  per real order line.

Note: users need the *Custom BAST / User* group to open the generated
documents (model access is owned by ``custom_bast``).
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Custom Platform/Operations",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": ["sale", "custom_bast"],
    "capability_tags": ["audit-trail", "sales"],
    "data": [
        "views/sale_order_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
