# Threat model — BCT analytics platform

Owner: Security agent. Status: **living document, reviewed at every gate.**
Scope: the Odoo 19 CE stack, the CDC pipeline, the warehouse, `login-gateway`,
`semantic-api` and `insight-portal` as described in `docs/agents/PLAN.md`.

This is written at Phase 1, when three of those six components do not exist yet. That is
the point. A threat model written after the pipeline is running documents what was built;
this one is a constraint on what gets built.

---

## 1. What we are protecting

| Asset | Why it matters | Where it lives |
|---|---|---|
| Personal data of Indonesian data subjects | UU 27/2022 (PDP) obligations, criminal liability for the controller | Odoo Postgres, then a **second copy** in the warehouse |
| Specific personal data (NIK, NPWP, bank, PPOB `customer_ref`) | Art. 4(3) — higher duty of care, higher harm on disclosure | Same, plus every backup |
| Tenant separation | One tenant reading another's revenue is a commercial and legal incident, not a bug | Every layer below the browser |
| Odoo availability | It is the operational ERP. Analytics is derived; the ERP is the business | The `odoo19-bct` compose stack |
| RS256 signing key | Whoever holds it mints sessions for any tenant, any role | `login-gateway` only |
| Per-tenant masking salt | Recovers plaintext from every `personal`/`sensitive` hash in the warehouse | SOPS, never a file, never git |

## 2. Trust boundaries

```
  browser ──①──> insight-portal (server) ──②──> semantic-api ──③──> warehouse
                        │                                              ▲
                        └──④──> login-gateway ──⑤──> Odoo JSON-RPC     │
                                                                       │
  Odoo Postgres ──⑥──> replication slot ──⑦──> CDC loader ──(masking)──┘
```

① The only boundary an attacker reaches without credentials. Everything server-side.
② Service-to-service; carries a token, never a connection string.
③ Where `tenant_id` must become both a bound parameter and an RLS session variable.
④ Authentication. ⑤ Odoo is the identity source of truth.
⑥ **A write-path dependency created by a read-only consumer** — see T-2.
⑦ The last point at which unmasked personal data legitimately exists.

---

## 3. Threats, ranked by expected harm

### T-1 — Cross-tenant read (PRIMARY confidentiality risk)

**Threat.** A session scoped to tenant A retrieves tenant B's rows: through a `tenant_id`
taken from a request header instead of the token, a query that filters in the application
but not in the database, a warehouse role that can see every schema, or an `.sudo()` in
Odoo that bypasses the Operating Unit record rules.

**Why it ranks first.** It is silent. There is no crash, no alert, no angry user — just a
number on a dashboard that belongs to someone else. It can run for months, and the first
evidence is usually a customer noticing.

**Controls, defence in depth (no single one is trusted):**

| # | Control | Enforced by | Verified by |
|---|---|---|---|
| 1 | `tenant_id` only from the verified JWT | contract 02 | semgrep `bct-contract02-tenant-id-from-request*` — **CI, every commit** |
| 2 | Bound parameter **and** Postgres RLS session variable | contract 02 §3 | Phase 3/4 tests; §6 "cross-tenant returns 403" |
| 3 | Operating Unit record rules in Odoo | `custom_operating_unit` | module test: OU-A user cannot read an OU-B `sale.order` |
| 4 | `.sudo()` on tenant-scoped models flagged | semgrep `bct-odoo-sudo-on-tenant-scoped-model` | CI |
| 5 | `warehouse_reader` holds `SELECT` + `REPLICATION` only | `postgres/init/sql/20-roles.sql` | Platform-Infra evidence: `INSERT` denied |
| 5b | Warehouse roles split three ways, all `NOSUPERUSER NOBYPASSRLS` | `analytics/warehouse/init/sql/20-schemas-roles.sql` | Adopted 2026-08-31; DWH evidence at GATE 3 |
| 6 | 403 leaks nothing about whether tenant B exists | contract 02 | Phase 4 test |

**A superuser ignores RLS. This is the fact that makes control 5b necessary.** In
Postgres, a role with `SUPERUSER` — or with `BYPASSRLS` — is exempt from row security
unconditionally, with no error and no log line. Point semantic-api at such a role and every
cross-tenant test still passes, because the policy is never evaluated: the test proves the
query is well-formed, not that isolation works. The Data Warehouse agent separated three
roles to make that structurally impossible — `warehouse_admin` (superuser; DDL and backups
only), `warehouse` (schema owner, dbt), and `warehouse_rls` (SELECT only, `NOSUPERUSER
NOBYPASSRLS`) — and `warehouse_rls` is the only identity semantic-api may use. Security
adopted this on 2026-08-31; the passwords are in `.secrets.enc.yaml`, `changeme` in
`.env.example`.

**Therefore, at GATE 3 the tenant-isolation test must prove which role it ran as.** A
passing isolation test executed as a superuser is indistinguishable from a passing one
executed under a correct policy, so the evidence must include
`select current_user, rolsuper, rolbypassrls from pg_roles where rolname = current_user;`
alongside the 403. Without that line the test result carries no information.

Keep it in that **column** form. `rolsuper` and `rolbypassrls` are booleans, and Postgres
renders them as `true`/`false` when concatenated with `||` — not the `t`/`f` that psql's
table output displays. A check written as `... = 'f'` against a concatenated string never
matches, so it passes forever without ever testing anything: a verification step that
cannot fail is worse than no verification step, because it is mistaken for one. Platform-
Infra hit exactly this while adopting the rule into `warehouse-reader-check.sh` and lost a
cycle to it. Returning columns rather than a built string sidesteps it entirely.

The same asymmetry is worth stating in general terms, because it decides where identity
assertions are needed at all: **connecting as a superuser by mistake makes every write
succeed, so that error catches itself. Pointing an isolation test at one makes the test
still pass, so that error never does.** The loud failure needs no control; the silent one
needs the identity line.

**Residual risk.** RLS is only as good as the session variable being set on *every* code
path, including background jobs and cache warmers that have no request context. Phase 3
must state how a connection-pooled query proves its session variable was set for the right
tenant — a pooled connection reused across tenants without a reset is the classic defeat
of RLS. **Security will hold this open at GATE 3.**

### T-2 — The replication slot turns a warehouse outage into an Odoo outage (AVAILABILITY)

**Threat.** Logical replication requires Postgres to retain WAL until the consumer
confirms it. The CDC loader stops — crash, bad deploy, a `docker compose down` on the
analytics stack, a schema change it cannot parse — and the slot stops advancing. Postgres
retains WAL indefinitely. The Odoo volume fills. **Postgres refuses writes and the ERP
stops.**

This is the highest-availability-impact risk in the design, and its direction is
counter-intuitive: the analytics system is architecturally read-only, has no write path to
Odoo, and can still take Odoo down. "Read-only" describes data flow, not blast radius.

**Controls:**

| # | Control | Enforced by | Status |
|---|---|---|---|
| 1 | `max_slot_wal_keep_size = 2GB` — a bounded cap; Postgres invalidates the slot rather than filling the disk | `postgres/postgresql.conf` | **live at first boot**, Platform-Infra evidence |
| 2 | Alert on replication lag and slot retention before the cap | Phase 3 observability | owed by DWH |
| 3 | Documented recovery: an invalidated slot means a full re-snapshot, not data loss | Phase 3 runbook | owed by DWH |
| 4 | Disk-usage alert on the Odoo Postgres volume | Phase 3 observability | owed by DWH |

**Accepted trade-off, explicitly.** Control 1 chooses *analytics correctness loss* over
*ERP outage*: past 2 GB the slot is invalidated and the warehouse needs a re-snapshot. That
is the right trade — the ERP is the business, analytics is derived — but it is a trade, and
whoever operates this must know it is there. **The alert (control 2) is what makes it
survivable; without it the first symptom is a re-snapshot nobody expected.**

### T-3 — The warehouse is a second copy of personal data (COMPLIANCE, UU 27/2022)

**Threat.** Every row copied out of Odoo doubles the exposed surface: a second database, a
second set of credentials, a second backup regime, a second retention policy, a second
place a subject-access or erasure request must reach. Under UU 27/2022 the controller's
obligations follow the data; they do not stop at the ERP boundary.

**Controls:**

| # | Control | Enforced by |
|---|---|---|
| 1 | Masking applied **during load**, before the row lands in `raw_` | contract 01; anti-pattern §7.5 |
| 2 | `personal` → deterministic HMAC-SHA256 with a per-tenant salt (joins survive, readability does not) | contract 01 |
| 3 | `sensitive` → HMAC, free text to NULL | contract 01 |
| 4 | `secret` → **never selected**, structurally incapable of landing | contract 01; semgrep `bct-contract01-select-star-from-odoo-source` |
| 5 | Unclassified column = loader hard-fails, never defaults to `public` | contract 01 |
| 6 | Salt in SOPS only | `.sops.yaml`, gitleaks `bct-warehouse-mask-salt` |
| 7 | Personal data kept out of logs | semgrep `bct-contract01-personal-field-in-log` |

**Residual risk — stated because it is easy to miss.** A deterministic hash is
pseudonymisation, **not anonymisation**. For a low-cardinality, known-format identifier
(phone, NIK) an attacker with the salt, or with the ability to submit chosen plaintexts,
can rebuild the mapping by enumeration. The salt is therefore key material and its
compromise is a personal-data breach, not a configuration problem. Salt rotation
invalidates historical joins and is a migration.

**The in-Odoo mask is a UI-and-RPC-surface control, and only that.** `custom_pdp_masking`
overrides `read()`, which is the funnel `web_read()` and `web_search_read()` use, so list,
form and kanban views are covered for web and RPC clients alike. It does not stop, and is
not intended to stop, a Settings administrator, a server action calling `sudo()`, or anyone
with database access. Odoo's Settings access is effectively root; no record rule or field
mask is a control against it. Saying so is what makes the control trustworthy.

**Open finding, raised to Platform-Addons 2026-08-31 — `export_data` bypasses the mask.**
Verified against the pinned Odoo 19 image: `odoo/orm/models.py:880` `export_data()` calls
`_export_rows()`, which reads values with `value = record[name]` (line 129) — ORM
`__getitem__`, straight to the cache, never through the overridden `read()`. A user without
`group_pdp_data_viewer` who holds `base.group_allow_export` can select records in a list
view and export unmasked names, emails and `customer_ref` to CSV. Export rights and
PDP-viewer rights are independent groups. This is the bulk path and it is the event UU
27/2022 cares most about — an export is a copy of personal data leaving the system.
**Security wants this closed before the CDC loader starts extracting**, with a test
asserting a non-viewer's export of `res.partner` contains no cleartext email.

**Accepted, documented: `group_operating_unit_all` is granted to `base.user_admin` at
install.** Removing it was considered and rejected — a fresh database would be
unadministrable, and the operator's first act would be to add themselves to the bypass group
via Settings, which teaches self-granting as a normal setup step and reaches the same end
state with less visibility. OU segmentation is intra-tenant; tenant separation is per
database plus warehouse RLS (contract 02), so this does not touch T-1. The related **defect**
raised to Platform-Addons: the grant lives in a `noupdate="0"` data block, so an operator who
revokes it gets it silently re-granted by the next `odoo -u custom_operating_unit`. A control
that un-revokes itself during routine maintenance is worse than one that was never applied.
Fix is to move the `user_ids` seed into `noupdate="1"`.

**Open at GATE 3:** warehouse backups contain the same pseudonymised data and are
frequently the least-protected copy. Who encrypts them, with what key, and what is the
retention period?

### T-4 — Compromise of the JWT trust boundary (contract 02)

**Threat.** Four concrete variants, in descending likelihood:

1. **Algorithm confusion.** A verifier that does not pin `algorithms` will accept an HS256
   token signed with the RSA *public* key it fetched from JWKS. The public key is public.
   Anyone can mint any claim set.
2. **`alg: none`.** A library that honours it accepts an unsigned token.
3. **Unverified decode.** `jwt.decode(token)` for "just the claims" — the claims are then
   whatever the caller typed, including `tenant_id` and `allowed_ou`.
4. **Signing key exfiltration** from `login-gateway` — total compromise of every session.

**Controls:**

| # | Control | Enforced by | Verified by |
|---|---|---|---|
| 1 | RS256 pinned; HS256/none rejected | contract 02 | semgrep `bct-contract02-jwt-weak-algorithm`, `-jwt-verify-without-algorithm-pin` — CI |
| 2 | Verification never disabled | contract 02 | semgrep `bct-contract02-jwt-verification-disabled` |
| 3 | Verifiers hold only the public key (JWKS); only the gateway holds the private key | contract 02 | design; Phase 3 review |
| 4 | Private key mounted by path, never in an env var or the image | `.env` conventions | gitleaks `bct-login-gateway-jwt-signing-key` |
| 5 | `exp` 3600 s; refresh cookie httpOnly + Secure + SameSite=Strict | contract 02 | semgrep `bct-ts-cookie-missing-httponly` |
| 6 | No token ever reaches the browser | master prompt §4 | semgrep `bct-ts-next-server-secret-to-client` |

**OPEN, RELEASE-BLOCKING (raised 2026-08-31): `login-gateway` and `semantic-api` ship a
PyJWT with an authentication-bypass CVE.** Found by scanning the images at registration
rather than by reading their Dockerfiles:

| CVE | Package | Installed | Fixed in |
|---|---|---|---|
| **CVE-2026-48526** | **PyJWT** | 2.10.1 | **2.13.0** — *authentication bypass due to forged JWTs* |
| CVE-2026-32597 | PyJWT | 2.10.1 | 2.12.0 — accepts unknown `crit` header extensions (RFC 7515 §4.1.11 MUST violation) |
| GHSA-537c-gmf6-5ccf | cryptography | 44.0.0 | 48.0.1 — vulnerable OpenSSL bundled in the wheels |
| CVE-2026-26007 / -69247 / -69249 | cryptography | 44.0.0 | 46.0.5 / 50.0.0 / 49.0.0 |
| CVE-2026-48818 / -54283 / CVE-2025-62727 | starlette | 0.41.3 | 1.1.0 / 1.3.1 / 0.49.1 |

Nine HIGH, every one with a fix available, in the two services that *are* the trust
boundary — one issues the tokens, the other verifies them. A forgery bypass in the JWT
library is T-4 reached by a route none of our own controls can close: pinning RS256
correctly, checking `iss`/`aud`, and holding only the public key all remain true and none
of them help if the library itself can be induced to accept a token the gateway never
signed.

**Two OpenSSLs, and the patched one is not the one doing the crypto.** The images
correctly `apt-get --only-upgrade` the OS openssl to 3.5.7 — so `python3 -c "import ssl"`
reports 3.5.7 and the claim "openssl verified in the built layers" is true. But
`cryptography`'s wheels statically bundle their own, and that one is **OpenSSL 3.4.0 (22
Oct 2024)**, verified in the built image via
`cryptography.hazmat.backends.openssl.backend.openssl_version_text()`. It is the bundled
copy that performs RS256 verification. Patching the OS layer says nothing about it.

Generalised for the gate checklist: **an image can contain more than one copy of a
security-critical library, and the OS package manager only knows about one of them.**

Caught independently by two CI jobs, which is the point of having both: `container-scan`
(Trivy, 9 HIGH) and `sca-python` (pip-audit, 29 advisories across 3 packages).

**Residual risk.** There is no key rotation story yet. JWKS supports `kid`-based rotation;
nothing in Phase 1–4 exercises it, and a gateway that cannot rotate its key has no
recovery path from variant 4 short of a full redeploy. **Security raises this at GATE 3
for the Backend agent: publish two keys in JWKS from day one, even if only one signs.**

### T-5 — Supply chain

**Threat.** A dependency, base image or GitHub Action introduces hostile code. The 2024
`xz-utils` backdoor and repeated `tj-actions/changed-files` tag-moving incidents are the
shape of this: not a bug in our code, and invisible to code review.

**Controls:** every action SHA-pinned (25 `uses:`, zero tags); every scanner binary pinned
by version **and** SHA-256; base images by digest (PLAN.md); `npm ci --ignore-scripts` so
install scripts cannot execute on a runner holding our source; `forbid-submodules`;
`permissions: contents: read` at workflow level; SBOMs for every image and project.

**Residual risk.** No signing or provenance attestation yet — that is Phase 5
(cosign/SLSA). Until then we can say what went into an image but not prove who built it.

### T-6 — Secret sprawl

**Threat.** The stack needs a dozen credentials. Every one is a candidate for a `.env`
committed "temporarily", a password in a compose file, or a real value in `.env.example`.

**Controls:** SOPS/age (`.sops.yaml`); `changeme` and nothing else in `.env.example`;
gitleaks over the working tree **and full history** in CI, plus a staged-diff pre-commit
hook; project-specific rules for the Odoo master password, mask salt, DSNs and age keys;
`forbid-plaintext-secret-files` refuses `.env`/`*.pem`/`*.key`/`id_rsa` outright.

**Residual risk.** gitleaks over history only protects a history we control. If this
repository ever gains a remote and a secret has already been pushed, rotation is the only
remedy — scrubbing history does not un-fetch it.

---

## 4. Explicitly out of scope for Phase 1

Named so nobody assumes they are handled: DDoS and rate limiting; physical/host security
of the Biznet Gio VPS; the browser's own security (extensions, XSS in third-party
scripts); insider threat by an operator with production database access; and log
retention/SIEM. Each needs an owner before production, none is a Phase 1 deliverable.

---

## 5. Gate checklist for the Security agent

At every gate, including phases Security did not build (master prompt §2.4 — Security has
veto and the Lead does not override it):

- [ ] Does anything new read `tenant_id` from somewhere other than the verified token? (T-1)
- [ ] Does a new consumer create a replication slot? Is its retention bounded and alerted? (T-2)
- [ ] Does a new column reach the warehouse without a classification? (T-3)
- [ ] Does a new read path bypass the mask? `read()` is not the only funnel — `export_data`
      already does. Check exports, reports, QWeb templates and any new controller. (T-3)
- [ ] Does the image contain a SECOND copy of a security-critical library that the OS
      package manager does not see? `cryptography` wheels bundle their own OpenSSL; an
      `apt --only-upgrade openssl` says nothing about it. (T-4, T-5)
- [ ] Is every column whose transform is `hmac_sha256` actually of a **text** type? A
      column can be correctly classified and still be unhashable - contract 01's
      "hard-fail on unclassified" does not catch it, so it surfaces as a silent wrong
      answer rather than a refusal to start. (T-3)
- [ ] Did a contract amendment reach its **producer**? A ruling written into
      `docs/agents/contracts/` is not in force until the module that declares the data
      agrees with it. Check the seed data, not the prose. (T-3)
- [ ] Does a security grant sit in a `noupdate="0"` block, so revoking it does not survive
      the next module upgrade? (T-1, T-3)
- [ ] Does any new verifier pin RS256 and check `iss`/`aud`? (T-4)
- [ ] Is every new image in `security/scan-targets.yml`, and every action SHA-pinned? (T-5)
- [ ] Did a new `.env` variable land in `.env.example` as anything other than `changeme`? (T-6)
- [ ] Has any suppression expired? (`python3 security/check_ignore_policy.py`)
