# -*- coding: utf-8 -*-
{
    "name": "Custom Finance Portal — SSO (Keycloak)",
    "summary": "Keycloak SSO login + role→group mapping (employee vs vendor) for the Finance Portal",
    "description": """
Custom Finance Portal — SSO (Keycloak)
======================================

Single sign-on for the Finance Portal via **Keycloak** (OIDC). Built on Odoo's
standard ``auth_oauth`` pointed at Keycloak's OpenID-Connect endpoints, plus:

- A seeded (disabled) ``auth.oauth.provider`` for Keycloak — set the realm
  endpoints + client id and enable per tenant.
- ``res.users`` role mapping: on OAuth sign-in, Keycloak roles/groups claims are
  mapped to Finance Portal groups (manager / finance officer / tax / requester /
  vendor) via a configurable JSON map (``custom_finance_portal_sso.role_group_map``).
- Employee vs **vendor** distinction (vendor role implies the portal group).

Hardening path: vendor the OCA ``auth_oidc`` module into ``addons/_vendor`` for
strict id_token + JWKS validation; the role-mapping override here is unchanged.
See ``MODULE_KNOWLEDGE.md`` for the Keycloak realm + token-mapper setup.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Accounting/Finance",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_finance_portal",
        "auth_oauth",
    ],
    "capability_tags": ["finance-portal", "sso", "keycloak", "oidc"],
    "data": [
        "data/auth_oauth_provider_data.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
