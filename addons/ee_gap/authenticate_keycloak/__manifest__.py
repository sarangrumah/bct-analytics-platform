# -*- coding: utf-8 -*-
{
    "name": "Auth Keycloak — Authorization Code Flow",
    "summary": "Adds the OAuth2 authorization-code flow (confidential client) to "
    "Odoo's auth_oauth, so Keycloak clients with Client Authentication on can log in",
    "description": """
Auth Keycloak — Authorization Code Flow
=======================================

Stock ``auth_oauth`` only implements the **implicit/token** flow
(``response_type=token`` is hardcoded in its ``list_providers``). A Keycloak
client configured as *confidential* (Client Authentication **on**, Standard
Flow **on**) must use the **authorization-code** flow and exchange the code for
a token with its ``client_secret``. This module fills exactly that gap.

What it adds
------------
- ``auth.oauth.provider.flow``: ``token`` (stock behaviour, default) or
  ``authorization_code``.
- ``auth.oauth.provider.token_endpoint`` plus a per-tenant **client secret**
  stored **encrypted** (Fernet) via ``custom.ir.config`` — never in process
  env vars, never plaintext.
- ``/auth_keycloak/code``: the redirect URI. It exchanges the code for an
  access token, then hands straight over to stock ``res.users.auth_oauth()``
  and ``session.authenticate()``.

What it deliberately does NOT do
--------------------------------
- **No login bypass.** Sign-in goes through Odoo's standard ``oauth_token``
  credential path. This module never calls ``authenticate()`` with an empty
  password, and adds no "trust this user" flag.
- **No HR sync.** Linking ``hr.employee``, NIK/department claims and HC API
  enrichment live in ``custom_hr_sso_keycloak``, which overrides
  ``_auth_oauth_signin`` — the method stock ``auth_oauth()`` already calls.
  That module is intentionally NOT a dependency (SSO must work without HR); when
  both are installed the sync runs automatically. Duplicating it would drift.

Setup (per tenant)
------------------
Settings → Users & Companies → OAuth Providers → *Keycloak SSO*: set the realm
endpoints and client id, switch **Flow** to *Authorization Code*, fill the
**Token URL** and **Client Secret**, then tick *Allowed*. The Keycloak client's
valid redirect URI must be ``<odoo-base>/auth_keycloak/code``.
    """,
    "author": "Achmad Rynaldi, Platform Team",
    "category": "Custom Platform/EE Gap",
    "version": "19.0.2.0.0",
    "license": "LGPL-3",
    "depends": ["base", "web", "base_setup", "auth_signup", "auth_oauth", "custom_core"],
    "data": [
        "views/auth_oauth_provider_views.xml",
    ],
    "installable": True,
    "capability_tags": ["sso", "keycloak", "oauth2", "multi-tenant"],
}
