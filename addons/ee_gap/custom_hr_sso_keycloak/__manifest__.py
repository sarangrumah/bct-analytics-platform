# -*- coding: utf-8 -*-
{
    "name": "Custom HR — SSO (Keycloak) + Employee Sync",
    "summary": "Keycloak SSO on Odoo auth_oauth; links & syncs hr.employee from "
    "claims + HC API, per-tenant, non-blocking",
    "description": """
Custom HR — SSO (Keycloak) + Employee Sync
==========================================

Single sign-on for Odoo HR via **Keycloak** (OIDC), built on Odoo's standard
``auth_oauth`` (per-tenant ``auth.oauth.provider`` records — no process-global
env vars), plus an HR sync that runs **after** authentication and never blocks
login:

- A seeded (disabled) ``auth.oauth.provider`` for Keycloak — set the realm
  endpoints + client id and enable per tenant.
- ``res.users._auth_oauth_signin`` override: adopt an existing local account by
  email (login == email) on first SSO so no duplicate user is created, then hand
  off to ``hr.sso.sync``.
- ``hr.sso.sync``: links the ``hr.employee`` by ``work_email``, fills NIK
  (``x_custom_nik``) / department from Keycloak claims, then enriches
  department / job / manager from an external HC API. Idempotent (only fills
  empty fields); a missing ``hr`` install or an HC-API outage is a no-op.

Multi-tenant safe: all config (provider, HC API base url, encrypted HC API key,
JIT toggle, claim names) lives in the tenant DB.

Hardening path: vendor OCA ``auth_oidc`` into ``addons/_vendor`` for strict
id_token + JWKS validation; the override and sync here are unchanged. See
``MODULE_KNOWLEDGE.md``.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Human Resources",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",  # custom.ir.config encryption helpers + Settings anchor
        "auth_oauth",  # Odoo 19 CE core — the auth foundation
        "hr",  # hr.employee / hr.department / hr.job
    ],
    "capability_tags": ["hr", "sso", "keycloak", "oidc", "multi-tenant"],
    "data": [
        "data/auth_oauth_provider_data.xml",
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
