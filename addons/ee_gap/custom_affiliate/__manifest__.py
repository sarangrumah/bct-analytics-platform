# -*- coding: utf-8 -*-
{
    "name": "Custom Affiliate",
    "summary": "Affiliate program: tracked links, click capture, order attribution, commission & payouts",
    "description": """
Affiliate marketing for the platform (Odoo CE has none). Every shared link
embeds the affiliate's identifier so any resulting order is attributed back to
them.

- ``custom.affiliate``        — affiliate master (unique code, commission rate, state)
- ``custom.affiliate.link``   — tracked links (short_code + UTM) for any target URL
- ``custom.affiliate.click``  — click analytics (hashed IP/UA, no raw PII)
- ``custom.affiliate.conversion`` — per-order attribution + commission lifecycle
- ``custom.affiliate.payout`` — periodic settlement batches
- ``sale.order``              — ``custom_affiliate_code`` / ``custom_affiliate_id``

Attribution model, window, default commission and reversal window are all
``ir.config_parameter`` driven (last-click / 30d / flat % / 14d by default).
""",
    "author": "Custom Platform",
    "category": "Sales/Affiliate",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": ["sale", "mail"],
    "capability_tags": ["affiliate", "marketing", "attribution"],
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence.xml",
        "data/ir_config_parameter.xml",
        "data/ir_cron.xml",
        "views/affiliate_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
