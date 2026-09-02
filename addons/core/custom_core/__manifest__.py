# -*- coding: utf-8 -*-
{
    "name": "Custom Core",
    "summary": "Shared utilities, mixins, and policy helpers for the Custom platform",
    "description": """
Foundational module for the Custom Odoo 19 Platform.

Provides:

- `custom.mixin.platform`: marker mixin that enforces the `x_custom_` prefix policy
  for fields added on top of Odoo core models (PDP-relevant relations).
- `custom.ir.config`: helper service to encrypt/decrypt parameters via Fernet
  (keys backed by env-injected master key).
- `custom.security`: HMAC signing helper used by `custom_ai_bridge` and the
  Coretax adapter abstractions.
- Settings page anchor under Settings > Custom Platform where downstream
  modules attach their toggles.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Hidden/Core",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": ["base", "web", "mail"],
    "capability_tags": ["multi-tenant", "audit-trail"],
    "data": [
        "security/custom_security.xml",
        "security/ir.model.access.csv",
        "views/res_config_settings_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
