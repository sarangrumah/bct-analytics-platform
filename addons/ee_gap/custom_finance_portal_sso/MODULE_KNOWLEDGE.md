# custom_finance_portal_sso — Keycloak SSO

SSO login for the Finance Portal. Built on Odoo standard `auth_oauth` against
Keycloak's OIDC endpoints, plus role→group mapping.

## Keycloak setup (per tenant)

1. **Realm**: e.g. `erp`. Two ways to split employees vs vendors:
   - separate realms (`erp` employees, `erp-vendor` vendors), or
   - one realm with mutually-exclusive roles (`finance_*` vs `finance_vendor`).
2. **Client**: `finance-portal`, type *OpenID Connect*, standard flow enabled,
   valid redirect URI `https://<odoo-host>/auth_oauth/signin`.
3. **Roles**: `finance_manager`, `finance_officer`, `finance_tax`,
   `finance_requester`, `finance_vendor`. Assign to users/groups.
4. **Token mapper**: add a *User Realm Role* (or *Group Membership*) mapper so the
   **userinfo** endpoint returns the roles. The mapping reads, in order:
   `realm_access.roles`, `groups`, `roles`, `resource_access.*.roles`.

## Odoo setup

1. Install `custom_finance_portal_sso`.
2. Settings → Users → OAuth Providers → **Keycloak SSO**: set realm host +
   `client_id`, tick **Allowed**.
3. (Optional) Override the role map:
   `ir.config_parameter` key `custom_finance_portal_sso.role_group_map` = JSON,
   e.g. `{"fin-admin": "custom_finance_portal.group_finance_manager"}`.

The role mapping runs in `res.users._auth_oauth_signin` and is additive (grants
groups; does not revoke). Vendor role implies `base.group_portal`.

## Hardening: strict OIDC via auth_oidc

`auth_oauth` validates via the userinfo endpoint. For id_token signature + JWKS
validation, vendor the OCA `auth_oidc` module into `addons/_vendor` (same as
`auth_jwt`), switch the provider to the OIDC flow, and keep this module's
role-mapping override unchanged.
