# -*- coding: utf-8 -*-
{
    "name": "Custom Adapter Framework",
    "summary": "Generic adapter registry, HTTP client, circuit breaker, and audit log for external integrations",
    "description": """
Foundation module providing a reusable adapter pattern for outbound integrations
(Coretax, Pajakku, Bank H2H, PPOB providers, etc.).

Provides:

- `custom.adapter.config`: per-tenant per-adapter configuration record
- `custom.adapter.call.log`: append-only call log
- `BaseAdapter`: Python base class with HTTP, HMAC signing, retry with exponential
  backoff, and a closed/open/half-open circuit breaker
- `@register_adapter(name)` decorator + `get_adapter_class(name)` resolver
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Custom Platform/Core",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": ["custom_core", "custom_pdp_audit"],
    "capability_tags": ["audit-trail", "multi-tenant", "approval-workflow"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/adapter_config_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
