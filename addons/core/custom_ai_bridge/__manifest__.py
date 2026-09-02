# -*- coding: utf-8 -*-
{
    "name": "Custom AI Bridge",
    "summary": "Connects Odoo to the platform AI gateway (Claude / OpenAI / Ollama abstraction)",
    "description": """
Bridge between Odoo and the platform AI gateway service.

Features:

- ``custom.ai`` service: ``chat()``, ``recommend(model, res_id, payload)`` calls
  the gateway with HMAC signing.
- Settings UI under Custom Platform > AI Intelligence for provider override,
  default model, quality tier, per-model on/off.
- "Ask AI" wizard that can be invoked on any record to get a structured
  recommendation from the gateway.

Depends on `custom_core` for HMAC helper and encrypted config storage.
""",
    "author": "Custom Platform",
    "category": "Custom Platform/AI",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": ["custom_core"],
    "capability_tags": ["ai", "multi-tenant"],
    "external_dependencies": {"python": ["httpx"]},
    "data": [
        "security/custom_ai_security.xml",
        "security/ir.model.access.csv",
        "data/custom_ai_data.xml",
        "views/res_config_settings_views.xml",
        "wizards/ai_recommend_wizard_views.xml",
    ],
    "installable": True,
    "application": False,
}
