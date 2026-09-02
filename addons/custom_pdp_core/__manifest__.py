{
    "name": "PDP Core - Field Classification Registry",
    "summary": "UU 27/2022 field classification registry: the single source of the data taxonomy.",
    "description": """
PDP Core
========

Implements frozen contract 01 (``docs/agents/contracts/01-classification.md``).

Holds ``pdp.field.classification``: one row per physical database column, carrying exactly one of
the five frozen PDP classes ``public | internal | personal | sensitive | secret``.

The CDC loader reads this registry over JSON-RPC at startup and refuses to start when a column it
is about to extract carries no classification. Unclassified is a hard failure, never a silent
default to ``public``.

Depends on ``base`` only, on purpose: the registry is a declarative catalogue keyed by model name
strings, so it can be installed into any database regardless of which business apps are present.
""",
    "version": "19.0.1.0.0",
    "category": "Productivity/Data Privacy",
    "author": "BCT Analytics Platform",
    "website": "https://example.invalid/bct",
    "license": "LGPL-3",
    "depends": ["base"],
    "data": [
        "security/pdp_groups.xml",
        "security/ir.model.access.csv",
        "data/pdp.field.classification.csv",
        "views/pdp_field_classification_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
