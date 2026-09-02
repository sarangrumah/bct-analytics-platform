# Auth Keycloak — Authorization Code Flow

Fills one gap in stock `auth_oauth`: it only speaks the **implicit/token** flow
(`response_type=token` is hardcoded in `list_providers`). A Keycloak client with
**Client Authentication on** (confidential) must use the **authorization-code**
flow and exchange the code for a token using its `client_secret`.

This module adds only that. Everything else is stock.

## How it fits together

```
login page ──► auth_link (response_type=code)      ← list_providers override
                        │
                        ▼
              Keycloak (realm, confidential client)
                        │  ?code=...&state={d,p,r}
                        ▼
        /auth_keycloak/code                        ← this module
                        │  POST token_endpoint (client_id + client_secret)
                        ▼  access_token
        res.users.auth_oauth(provider, {access_token})   ← STOCK
                        │  _auth_oauth_validate → userinfo
                        │  _auth_oauth_signin   → custom_hr_sso_keycloak
                        ▼                          (employee link, NIK, HC API)
        session.authenticate(type='oauth_token')   ← STOCK
```

## Boundaries (read before extending)

- **No login bypass.** Sign-in goes through Odoo's standard `oauth_token`
  credential. This module never calls `authenticate()` with an empty password
  and adds no "trusted user" flag. (v0.1 did both — that was an auth-bypass
  surface and is gone.)
- **No HR sync here.** `hr.employee` linking, NIK/department claims and HC API
  enrichment belong to `custom_hr_sso_keycloak`, which overrides
  `_auth_oauth_signin` — the method stock `auth_oauth()` calls. **That module is
  deliberately not a dependency**: SSO must work without HR. Install it too and
  the sync runs automatically; install this alone and you simply get SSO. Do not
  re-add that logic here — v0.1 duplicated it and the copies drifted.
- **No env vars.** Config is per-tenant on the `auth.oauth.provider` record; the
  client secret is encrypted (Fernet) via `custom.ir.config`. v0.1 read
  `KEYCLOAK_*` from process env, which cannot work on a DB-per-tenant platform.

## Setup (per tenant)

1. Keycloak client: Client Authentication **on**, Standard Flow **on**, valid
   redirect URI `<odoo-base>/auth_keycloak/code`. Copy the client secret.
2. Odoo → Settings → Users & Companies → **OAuth Providers** → *Keycloak SSO*:
   - **Client ID** — the Keycloak client id
   - **Flow** — *Authorization Code (confidential client)*
   - **Authorization URL** — `.../realms/<realm>/protocol/openid-connect/auth`
   - **Token URL** — `.../realms/<realm>/protocol/openid-connect/token`
   - **UserInfo URL** — `.../realms/<realm>/protocol/openid-connect/userinfo`
   - **Client Secret** — paste once; stored encrypted. Blank keeps the stored one.
   - **Allowed** — tick to show the button.
3. For employee sync, configure `custom_hr_sso_keycloak` (HC API base URL + key).

Existing providers keep `flow = token`, so installing this changes nothing until
a provider is explicitly switched.

## History

v0.1 (Achmad Rynaldi, Odoo 17) lived at `addons/authenticate_keycloak` — the
`addons/` root, which is **not** on `addons_path`, so it was never loadable. The
2026-07-16 rewrite moved it here, cut it down to the flow gap, and removed the
`res.users.authenticate` override, the `dotenv`/env config (there is no `dotenv`
in `odoo/requirements.txt`) and the duplicated HR sync.
