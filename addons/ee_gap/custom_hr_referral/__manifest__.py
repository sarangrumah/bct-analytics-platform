# -*- coding: utf-8 -*-
{
    "name": "Custom HR Referral",
    "summary": "Employee referral program with candidate tracking + reward ledger",
    "description": """
Employees submit candidate referrals against open positions; HR tracks
the candidate through screening → interviewed → offered → hired. Reward
is credited when state hits 'hired' (configurable per position).
""",
    "author": "Custom Platform",
    "category": "Human Resources/Recruitment",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": ["custom_core", "custom_pdp_audit", "hr", "mail"],
    "capability_tags": ["recruitment", "audit-trail", "pdp"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/referral_position_views.xml",
        "views/referral_candidate_views.xml",
        "views/referral_reward_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
