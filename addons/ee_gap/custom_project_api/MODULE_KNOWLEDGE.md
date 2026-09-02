---
status: reviewed
generated_at: 2026-07-30T00:00:00Z
generator: hand-written
module: custom_project_api
manifest_version: 19.0.1.0.0
---

# custom_project_api

## Purpose
The JWT + HMAC REST surface the headless Next.js app (`vas-pmo/`, port 18110) runs on.
Shaped like `custom_storefront_api` so the two front-ends are operated the same way.

## Auth
`auth.jwt.validator` named `pmo` (HS256, `aud=pmo`, `iss=custom_project_api`).

Unlike the storefront — where every caller is a customer mapping to one static internal
user — here the caller **is** an internal user and every write has to be attributed to them
in the audit trail. So the validator gains a `pmo_login` `user_id_strategy`: the token
carries `sub` = login and `_get_uid` resolves the real `res.users`. `user_id` in a request
body is never trusted.

Access token 15 minutes (`custom_project_api.access_ttl`). Refresh tokens are stored
**hashed** in `custom.pmo.token`, single-use (`_rotate` revokes as it issues), 14-day TTL,
purged daily.

Login answers identically for an unknown user and a wrong password — a different answer
hands an attacker a list of valid logins. `_password_ok` tries both `_check_credentials`
signatures (Odoo ≤17 took `(password, env)`, 18+ takes `(credential_dict, env)`) and
re-raises anything that is not a clean `AccessDenied`.

## Routes
| Route | Auth | Notes |
|---|---|---|
| `POST /pmo/api/auth/{login,refresh,logout}` | public | |
| `GET /pmo/api/auth/me` | jwt | roles + the verticals this user holds |
| `GET /pmo/api/health` | public | |
| `GET /pmo/api/meta/{stages,verticals}` | jwt | stage list carries `sla_clock` |
| `GET /pmo/api/projects` | jwt | health, progress, overdue/hold/waiting counts |
| `GET/POST /pmo/api/tasks`, `GET/POST/PATCH …/tasks/<id>` | jwt | write is field-allow-listed (`TASK_WRITABLE`) |
| `POST /pmo/api/tasks/<id>/{stage,comment}` | jwt | stage accepts `stage_code` |
| `GET /pmo/api/change-requests`, `POST …/<id>/action` | jwt | actions allow-listed |
| `GET /pmo/api/weekly`, `POST/PATCH …/weekly/<id>` | jwt | `submit: true` submits |
| `GET /pmo/api/dashboard/{summary,ba-summary}` | jwt | pure aggregates, no new tables |
| `GET /pmo/api/logs` | jwt | reads the hash-chained `pdp.audit.log` |
| `POST /pmo/api/hmac/{ping,notify-result}` | none + HMAC | machine-to-machine |
| `POST /pmo/api/hmac/tasks/<id>/verify` | none + HMAC | brand PIC clicking the WA link |

## Important behaviour
- `_guard` turns `UserError` / `ValidationError` into **422** and `AccessError` into 403, so
  a business rule reads as a rejection instead of a 500. An illegal stage transition comes
  back as `RULE_REJECTED` with Odoo's own message.
- Everything runs as the authenticated user, so record rules and the audit trail apply
  exactly as in the backend.
- `auth='none'` routes have no `env.user` in Odoo 19, so the HMAC verify endpoint writes
  explicitly as `SUPERUSER_ID` and names the brand contact in the audit `reason` — otherwise
  the trail would show an anonymous close.
- HMAC: `X-Signature = HMAC-SHA256(secret, ascii(X-Timestamp) + raw_body)`, five-minute
  replay window, `hmac.compare_digest`, secret in
  `custom_core.secure_endpoint.pmo.secret`.

## Gotchas
- The `ba-summary` aggregate must read `res.groups.all_user_ids`, not `user_ids`: in Odoo 19
  `user_ids` is direct members only and every BA reaches the group through `implied_ids`.
- The seeded JWT `secret_key` is a placeholder (`change-me-at-deploy-time`) and must be
  rotated from `VAS_PMO_JWT_SECRET` at deploy. A committed secret is not a secret.

## Integration Points
- **Depends on:** `custom_project_portfolio`, `custom_project_cr`, `custom_project_notify`,
  `auth_jwt` (vendored OCA).
- **Consumed by:** `vas-pmo/` (Next.js BFF).
