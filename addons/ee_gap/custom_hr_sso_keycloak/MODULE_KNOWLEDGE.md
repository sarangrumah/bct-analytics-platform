# custom_hr_sso_keycloak — Keycloak SSO + HR sync

Single sign-on for Odoo HR via **Keycloak** (OIDC), built on Odoo standard
`auth_oauth`, plus a non-blocking `hr.employee` sync from Keycloak claims and an
external HC API. Multi-tenant safe: every setting lives in the tenant DB (no
process-global env vars).

## How it works

1. `data/auth_oauth_provider_data.xml` seeds a **disabled** `auth.oauth.provider`.
2. On the login page the user clicks the Keycloak provider button → standard
   `auth_oauth` code/token exchange and userinfo call.
3. `res.users._auth_oauth_signin` (override in `models/res_users.py`):
   - **adopt-by-email** — links the Keycloak `sub` (`oauth_uid`) onto the existing
     local user matched by `login == email`, so no duplicate user is created;
   - if no local user and JIT is off (default) → blocked (`oauth_error=3`);
   - calls `hr.sso.sync.sync_for_login(login, validation)` in a `try/except` that
     only logs — **login is never blocked by a sync failure**.
4. `hr.sso.sync` (`models/hr_sso_sync.py`):
   - links `hr.employee` by `work_email == login` (no auto-create),
   - fills `x_custom_nik` (16-digit; only if `custom_hr_payroll_id` is installed)
     and `department_id` from the `nik` / `dept` claims,
   - enriches empty `department_id` / `job_id` / `parent_id` from the HC API
     (`GET {hc.base_url}api/v1/open-api/employees/{nik}`, header `X-API-Key`).
   - Idempotent: only ever fills empty fields.

## Keycloak setup (per tenant)

1. **Realm**: e.g. `erp`.
2. **Client**: `hr-portal`, type *OpenID Connect*, Standard Flow enabled, valid
   redirect URI **`https://<tenant-host>/auth_oauth/signin`** (Odoo's standard
   callback — not a custom path).
3. **Token / userinfo mappers** so the **userinfo** endpoint returns:
   - `email`, `name` (standard, via `openid email profile` scope),
   - **`nik`** — User Attribute mapper → claim `nik`, *Add to userinfo* = ON,
   - **`dept`** — User Attribute (or Group Membership) mapper → claim `dept`,
     *Add to userinfo* = ON.
   - `sub` is automatic and becomes Odoo's `oauth_uid`.
4. Provision the `nik` / `dept` user attributes (manually, user federation, or SCIM).

## Odoo setup (per tenant DB)

1. Install `custom_hr_sso_keycloak`.
2. **Settings → Users → OAuth Providers → “Keycloak SSO”**: set the realm host in
   the auth/validation/data endpoints + the `client_id`, tick **Allowed**.
3. **Settings → Custom Platform → HR SSO (Keycloak)**:
   - *Auto-create users (JIT)* — leave **off** to require pre-existing users.
   - *HC API Base URL* — `https://hc.example.com/` (trailing slash). Empty = skip
     enrichment.
   - *HC API Key* — stored **encrypted**. Requires the `CORETAX_SERTEL_MASTER_KEY`
     env (platform secret holder); leave blank to keep the existing key.
4. (Optional) Different claim names: set `ir.config_parameter`
   `custom_hr_sso_keycloak.claim_nik` / `claim_dept`.

## Notes & edge cases

- `hr` not installed → module still installs, SSO works, sync no-ops.
- `custom_hr_payroll_id` not installed → NIK isn't persisted, but department / job
  / manager still sync from the HC API (the claim NIK drives the API call).
- NIK claim that isn't exactly 16 digits is skipped + logged (never raises into login).
- HC API outage / missing config → logged no-op; login still succeeds.
- Email matching is case-insensitive (`=ilike`).

## Hardening: strict OIDC via auth_oidc

`auth_oauth` validates via the userinfo endpoint. For id_token signature + JWKS
validation, vendor the OCA `auth_oidc` module into `addons/_vendor` (add it to
`addons/_vendor/fetch_oca.sh`, re-run, then add `auth_oidc` to `depends`), switch
the provider to the OIDC flow, and store the client secret encrypted via
`custom.ir.config`. The `_auth_oauth_signin` override and `hr.sso.sync` are unchanged.

## Relationship to other modules

- Mirrors `custom_finance_portal_sso` (same `auth_oauth` + `_auth_oauth_signin`
  pattern); that module maps roles→groups for the Finance Portal, this one links &
  syncs HR employees. They can coexist (different concerns, same provider).
- Replaces the legacy `addons/authenticate_keycloak` (env-var config, broken
  password-less path). Retire that once this reaches production parity.
