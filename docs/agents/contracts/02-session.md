# Frozen contract 2 — session (Security → Backend → Frontend)

Status: **FROZEN at GATE 0.** Producer: `login-gateway`. Consumers: `insight-portal` (server side
only), `semantic-api`.

## Shape

`login-gateway` authenticates the user against Odoo over JSON-RPC (`common.authenticate`), reads the
user's company and Operating Unit assignments, and issues a **RS256** JWT. The gateway holds the
private key; every verifier fetches the public key from the gateway's JWKS endpoint and therefore
never holds signing material.

```json
{
  "iss": "https://login-gateway.local/",
  "aud": "insight-portal",
  "sub": "odoo:<database>:<uid>",
  "tenant_id": "acme",
  "odoo_uid": 7,
  "roles": ["analytics.viewer"],
  "allowed_ou": [1, 4, 9],
  "all_ou": false,
  "company_ids": [1],
  "iat": 1756600000,
  "exp": 1756603600
}
```

- `tenant_id` — the Odoo database this session belongs to. **Never** taken from a request header,
  query string, cookie or request body. Only from the verified token.
- `roles` — one of `analytics.viewer`, `analytics.analyst`, `analytics.admin`.
- `allowed_ou` — Operating Unit ids the user may see. **An empty array means the user sees only
  documents carrying no Operating Unit** — it does NOT mean "all". This mirrors
  `custom_operating_unit`'s record rules exactly, which fail closed.
- `all_ou` — boolean, the explicit bypass. `true` only for members of
  `custom_operating_unit.group_operating_unit_all`. **Absent or `false` means no bypass.**
- `exp` — 3600 s. Refresh via an httpOnly, `Secure`, `SameSite=Strict` refresh cookie.

## Verification, server-side only

1. Signature verified against JWKS, algorithm **pinned to RS256** — `alg: none` and HS256 confusion
   are rejected outright.
2. `iss` and `aud` checked exactly. `exp`/`nbf` checked with 30 s leeway.
3. `tenant_id` is injected into every warehouse query as a bound parameter **and** set as the
   Postgres session variable that RLS reads. Application-level filtering alone is not sufficient
   (master prompt §3.3).

The browser never receives a token that grants direct database or semantic-api access, never
receives a connection string, and never receives more rows than it renders (§4).

## Scope violation response

A session for tenant A requesting tenant B returns **HTTP 403** with exactly:

```json
{"error": "tenant_scope_violation", "detail": "Session is not scoped to the requested tenant."}
```

No leak of whether tenant B exists. The event is written to the audit log with the subject, the
requested tenant and the timestamp. Proven by test (§6: "Cross-tenant access returns 403").

## Amendment at GATE 3 — `allowed_ou: []` no longer means "all"

**This corrects a defect in the contract as originally frozen, found by the Backend agent.**

The contract said an empty `allowed_ou` meant *all OUs in the tenant*. The producer says the
opposite — `addons/custom_operating_unit/models/res_users.py:21-22`:

> "Empty means the user sees only documents that carry no Operating Unit — the rules fail closed,
> not open."

The same `[]` therefore meant "everything" to the gateway and "almost nothing" to Odoo. A user with
no OU entitlement would receive a token whose `allowed_ou` is `[]`, the semantic API would read that
as all-OUs, and **that user would see more in the dashboard than in Odoo**. A privilege escalation
manufactured purely by two documents disagreeing — and nothing would report it, because the token is
valid, the tenant check passes, and every row returned is genuinely in the right tenant.

### Ruling: the empty value is the restrictive one; "all" is explicit

`allowed_ou: []` now means *no Operating Units*, matching Odoo. The bypass is a separate boolean
`all_ou`, issued only to members of `group_operating_unit_all`.

Backend proposed keeping `[]` = all and guarding it by refusing to issue a token to anyone not in the
bypass group. That is sound and it closes today's hole. It was not taken because it leaves
"empty means everything" latent in the verifier forever: any future code path that forgets to
populate the claim, or populates it from a failed lookup, grants **everything**. With this ruling,
forgetting grants **nothing**.

That is the same principle this project already committed to with `warehouse_reader` — read-only *by
construction*, not by policy. A guard that must be remembered is weaker than a default that is safe.

**The wire format change is free right now and will not be later:** `insight-portal/` does not yet
exist, so no consumer has bound to the old shape. Deferring this would have made it expensive
exactly when it became load-bearing.

### Consequences
- The gateway may now safely issue a token to a user with no OU entitlement; it simply sees what that
  user sees in Odoo. Backend may still refuse instead, but that is now a UX choice, not a safety
  requirement — the ambiguity it was defending against is gone.
- Every verifier must treat **absent `all_ou` as `false`**. Never infer the bypass from emptiness.
- Contract 03's `allowed_ou` filtering and any RLS predicate must be updated to match.
