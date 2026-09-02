{
    "name": "PDP Masking - transforms and in-Odoo enforcement",
    "summary": "Masking policy for the five PDP classes, plus the reference HMAC implementation.",
    "description": """
PDP Masking
===========

Implements the masking half of frozen contract 01.

* ``pdp.masking.rule`` maps each of the five PDP classes to exactly one transform.
* ``pdp_hmac_sha256()`` in ``models/pdp_hash.py`` is the **reference implementation** of the
  deterministic digest. The Python CDC loader must reproduce it byte for byte; the exact
  construction (encoding, argument order, digest, hex casing) is specified in MODULE_KNOWLEDGE.md
  and pinned by a known-answer test.
* ``pdp.masked.mixin`` masks ``personal`` and ``sensitive`` columns in the Odoo UI for users
  outside the ``PDP / Data Viewer`` group.

Warehouse masking is applied by the CDC loader at load time, not here. This module exists so that
Odoo and the loader agree on *what* the transform is, and so that the Odoo UI is not a hole in the
same policy.
""",
    "version": "19.0.1.0.0",
    "category": "Productivity/Data Privacy",
    "author": "BCT Analytics Platform",
    "website": "https://example.invalid/bct",
    "license": "LGPL-3",
    "depends": ["custom_pdp_core"],
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "data/pdp_masking_rule_data.xml",
        "views/pdp_masking_rule_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
