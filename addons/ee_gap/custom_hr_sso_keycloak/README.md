# Custom HR — SSO (Keycloak) + Employee Sync

Keycloak SSO for Odoo HR on the multi-tenant platform, built on Odoo standard
`auth_oauth` (per-tenant `auth.oauth.provider` records — no env vars), with a
non-blocking `hr.employee` sync from Keycloak claims + an external HC API.

- **Login**: standard `auth_oauth` button → Keycloak OIDC.
- **Adopt-by-email**: first SSO login links the Keycloak identity onto the existing
  Odoo user (`login == email`); no duplicate users.
- **HR sync**: links `hr.employee` by `work_email`, fills `x_custom_nik` / department
  from claims, enriches department / job / manager from the HC API. Idempotent and
  never blocks login.

See [MODULE_KNOWLEDGE.md](./MODULE_KNOWLEDGE.md) for Keycloak realm/client/mapper
setup, per-tenant Odoo configuration, and the `auth_oidc` hardening path.
