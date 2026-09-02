# -*- coding: utf-8 -*-
{
    "name": "Custom Subscriptions",
    "summary": "Recurring billing, MRR/LTV analytics, AI churn prediction",
    "description": """
Custom Subscriptions — Phase 2E MVP:
- Subscription plans (daily / weekly / monthly / yearly)
- Subscription contracts with state machine (draft/active/paused/churned/closed)
- Recurring invoice generation cron
- MRR + LTV computed metrics
- AI churn prediction via custom_ai_bridge
""",
    "author": "Custom Platform",
    "category": "Sales/Subscriptions",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_ai_bridge",
        "sale_management",
        "account",
    ],
    "capability_tags": ["subscription", "recurring", "ai", "accounting"],
    "data": [
        "views/generated_search_views.xml",
        "security/custom_subscription_security.xml",
        "security/ir.model.access.csv",
        "data/subscription_cron.xml",
        "views/subscription_plan_views.xml",
        "views/subscription_contract_views.xml",
        "views/menu_views.xml",
        "data/subscription_sample_data.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
