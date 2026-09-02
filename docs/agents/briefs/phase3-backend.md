# Brief: Backend — Phase 3 (CDC pipeline, semantic layer, auth gateway)

## Objective
Three services that together make the warehouse live and safely queryable: a CDC consumer that
streams Odoo's WAL into the landing zone with masking applied *before* the row lands, a read-only
semantic API that is the single place business logic is defined, and a login gateway that turns an
Odoo credential into a scoped session token. Nothing downstream may hand-write SQL, and nothing
downstream may choose its own tenant.

## Read first — in this order
1. `docs/adr/0001-analytics-warehouse.md` — **Accepted at GATE 2, binding.** `pgoutput` logical
   replication (not a `write_date` tap, not Debezium), tombstones for deletes, per-mart freshness
   table, 2 GB slot cap, publication+slot per tenant.
2. `docs/agents/contracts/01-classification.md` — the five classes and their transforms.
3. `docs/agents/contracts/02-session.md` — **you are the producer of this contract.** RS256, JWKS,
   the exact claim set, the exact 403 body.
4. `docs/agents/contracts/03-metric.md` — the query API shape you must implement.
5. `docs/agents/contracts/04-platform.md` — roles, network, env names, reserved ports.
6. `docs/agents/contracts/05-warehouse.md` — DWH's `column_policy`, `pipeline_state`, `raw.*`
   conventions, RLS session variable.
7. `addons/custom_pdp_masking/MODULE_KNOWLEDGE.md` — the **exact HMAC construction** you must
   reproduce byte-identically in Python.

## Ground truth
Phase 1 gave you Postgres 16 with `wal_level=logical`, `max_slot_wal_keep_size=2GB`, and a
`warehouse_reader` role holding **only** `SELECT` + `REPLICATION`. Phase 3 DWH gives you the landing
schema and the column policy. Reserved ports, already allocated — do not take another:
warehouse-db `35433`, **login-gateway `38120`**, **semantic-api `38200`**, insight-portal `33000`.

## The seam with Data Warehouse
**DWH defines the masking policy; you apply it.** Read `warehouse.column_policy` at startup and
execute exactly what it says. Do not invent a transform, do not hardcode a classification, do not
add a class. If you find yourself editing `analytics/dbt/**` or `analytics/warehouse/**`, you have
drifted — raise it to the Lead.

## Scope — in

### A. CDC pipeline (`analytics/cdc/`) — build this first; nothing else in Phase 3 matters until data flows
Master prompt §3.0.

- Consume `pgoutput` logical decoding from the Odoo Postgres as `warehouse_reader`. **One publication
  and one replication slot per tenant database.**
- **Landing is append-only and unmodified** apart from `_ingested_at`, `_op` (`I|U|D`), `_tenant_id`,
  `_lsn`. All transformation happens *after* landing — except masking, which happens *before* it.
- **Masking on load, not later** (§3.2, anti-pattern §7.5). `personal` → deterministic
  `hmac_sha256(value, per_tenant_salt)`; `sensitive` → same, free text to NULL; `secret` → **never
  selected in the first place**, so it is structurally incapable of landing.
- **Hard-fail on an unclassified column.** If a column you are about to extract has no row in
  `warehouse.column_policy`, exit non-zero with a clear message. Never default to `public`.
- **Deletes become tombstones** (`_op='D'`). A `DELETE` in Odoo must remove the row from the mart
  within the freshness SLA.
- **Backfill and steady-state are separate code paths.** The initial snapshot must be **resumable** —
  a failure at 80% must not restart from zero. Prove it: kill it mid-run, restart, show it resumes.
- **Idempotent**: re-running over the same source range produces identical marts.
- Per-tenant salts come from the environment (SOPS-managed), never from a file, never in git,
  `changeme` in `.env.example`.
- **Prometheus metrics** — these are also what the dashboard's "last refreshed at" reads:
  rows/sec, end-to-end lag per table, **replication slot lag**, last successful run timestamp,
  failure count. Write progress to `warehouse.pipeline_state`.
- **Slot hygiene**: on clean shutdown, do not leave an inactive slot accumulating WAL. Document what
  happens if the consumer dies — the 2 GB cap is the backstop, but the consumer must not rely on it
  as normal operation.

### B. Semantic layer (`analytics/semantic-api/`) — §3.5
- FastAPI, read-only, the **single place each metric is defined**. The front-end must never
  hand-write business logic in SQL or TypeScript.
- Metric definitions in `metrics/*.yml` validated against `metrics/metric.schema.json`, exactly per
  contract 03. A metric failing the schema fails the build.
- `POST /v1/query` accepting `{metric, dimensions[], filters{}, order_by, limit}` and **compiling**
  it from the contract. **Never accept raw SQL.** Anything not declared is rejected `400` before a
  query is planned.
- Every response carries `meta.last_refreshed_at` and `meta.is_stale`, read from
  `warehouse.pipeline_state` — never from a clock.
- `tenant_id` is taken **only** from the verified JWT, never from a header, query string, cookie or
  body. It is bound as a parameter **and** set as the RLS session variable.
- Performs **no masking** and must be incapable of unmasking — the data is already masked upstream.
- `make metric-fixtures` generating `metrics/fixtures/*.json` for the Frontend agent, so Frontend
  never invents a data shape.
- A `/healthz` and a `/metrics` endpoint.

### C. Login gateway (`login-gateway/`)
- Authenticates against Odoo over JSON-RPC (`common.authenticate`), reads the user's company and
  `allowed_operating_unit_ids` from `custom_operating_unit`, issues the **RS256** JWT of contract 02.
- **Holds the private key; verifiers hold only the public key** via a JWKS endpoint. Never ship the
  signing key to a verifier.
- Refresh via httpOnly, `Secure`, `SameSite=Strict` cookie. 3600 s access token.
- Rate-limit authentication attempts. Do not log credentials, tokens, or the values of any
  `personal`/`sensitive` field.

## Security findings you MUST answer — raised at GATE 1, not optional

The Security agent reviewed this design and held these open against you. They are conditions of
passing GATE 3, not suggestions.

### T-1 — RLS is defeated by a pooled connection (blocking)
Postgres RLS reads a **session** variable. A connection pool that hands a connection carrying
`app.tenant_id = 'tenant_a'` to a request for tenant B silently serves A's rows to B — RLS will not
save you, because from the database's point of view nothing is wrong. Contract 02 does not say how
this is prevented. **You must.** Acceptable answers include `SET LOCAL` inside an explicit
transaction for every query (so the value cannot outlive the transaction), a pool keyed per tenant,
or resetting the variable on connection checkout and checkin with a guard that fails closed if it is
unset. State which you chose and prove it with a test that reuses a pooled connection across two
tenants and asserts no leakage.

### T-4 — no key-rotation story for the RS256 signing key (blocking)
Platform-Infra already ships `LOGIN_GATEWAY_JWT_KID` in `.env.example`, which makes rotation
possible. **Publish two keys in JWKS from day one** and have verifiers select by `kid`, so a rotation
is a config change rather than a flag-day outage. A single-key JWKS is a design you cannot rotate
without downtime.

### T-2 — informational, owned by DWH
The 2 GB slot cap trades analytics correctness for ERP uptime: past the cap the slot is invalidated
and the warehouse needs a re-snapshot. Your consumer must therefore **detect an invalidated slot and
report it loudly** rather than silently reconnecting and producing a mart with a hole in it.

## Scope — out
- `analytics/dbt/**`, `analytics/warehouse/**`, `docker-compose.analytics.yml`,
  `observability/*analytics-*` — **Data Warehouse agent.**
- `insight-portal/**` — Frontend agent. You produce fixtures for them; you do not write their code.
- `.github/workflows/**` — **Security owns `ci.yml`.** To add your images to `container-scan`, send
  the Lead a diff request for Security to merge (§2.1 conflict rule). Do not edit it.
- `addons/**` — Platform-Addons. If you need a field or an RPC method, raise it; do not add it.
- `docker-compose.yml`, `Makefile`, `odoo/**`, `postgres/**`, `scripts/**` except `scripts/analytics/`
  — Platform-Infra.

## Contracts consumed
`04-platform.md` (roles, env, ports, network), `05-warehouse.md` (column policy, pipeline_state,
raw conventions, RLS variable), `01-classification.md`, `03-metric.md`, and the HMAC spec from
`custom_pdp_masking`.

## Contracts produced — publish to `docs/agents/contracts/06-api.md`
- The **exact** `POST /v1/query` request and response schemas, including the `meta` block. Frontend
  builds against this and nothing else.
- The JWKS URL, token claim set, expiry, and the verbatim 403 body from contract 02.
- The fixture generation command and where fixtures land.
- Prometheus metric names the DWH agent's Grafana dashboard will graph — **agree these names with
  DWH through the Lead before publishing**, since DWH is building the panels that read them.

## Constraints
- **No write path from the warehouse into Odoo** (anti-pattern §7.10). You connect as
  `warehouse_reader`; the role makes writes impossible. Do not work around it.
- **Never query Odoo's OLTP Postgres to serve a dashboard request** (anti-pattern §7.3). The semantic
  API reads the warehouse only.
- The browser never receives a connection string, never receives a token granting direct DB access,
  and never receives more rows than it renders.
- Pin base images by digest: `python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217`,
  `node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32`.
- Non-root containers, `no-new-privileges`, `cap_drop: [ALL]`.
- **Other live Docker stacks must not be disturbed.** Always `-p odoo19-bct`. Never
  `docker system prune` / `volume prune` / unscoped `down`.

## Acceptance criteria — testable statements only
1. **Live sync proven end to end, with timestamps** (§6): create, update, then delete a record in
   Odoo; show it appear, change, and disappear from the mart within the stated SLA. Paste timestamps.
2. Backfill is resumable: kill the snapshot mid-run, restart, show it resumes rather than restarting.
3. Re-running the load over the same range yields identical marts.
4. The loader **exits non-zero** when a column has no classification. Prove it by removing one policy
   row.
5. A `secret`-class column is absent from the warehouse entirely.
6. `POST /v1/query` with an undeclared metric, dimension or filter returns `400`.
7. A raw-SQL-looking payload is rejected; there is no code path that executes caller-supplied SQL.
8. A token for tenant A querying tenant B returns **403** with exactly the contract-02 body.
9. A tampered token, an `alg: none` token, and an HS256-signed token are all rejected.
10. `meta.last_refreshed_at` changes after a new load and matches `warehouse.pipeline_state`.
11. Prometheus endpoint exposes slot lag, rows/sec, per-table lag, last success, failure count.
12. Killing the CDC consumer causes slot lag to grow and the alert to fire (coordinate with DWH, who
    owns the rule) — §6 requires this demonstrated, not just written.

## Evidence required — paste the output of exactly these
```
docker compose -p odoo19-bct ps
# live sync, with timestamps at each step
date -u +%FT%TZ; docker compose -p odoo19-bct exec -T odoo odoo shell -d erp_dev --no-http <<'EOF'
# create a uniquely-named record, print its id and write_date
EOF
date -u +%FT%TZ; docker compose -p odoo19-bct exec -T warehouse-db psql -U warehouse -d warehouse -c "select * from marts.fct_sale_order_line where <that record> ;"
# then update, re-query; then unlink, re-query and show it gone
curl -s -X POST localhost:38200/v1/query -H 'Authorization: Bearer <tenant_a>' -d '{"metric":"revenue_net","dimensions":["date_month"],"filters":{"date_range":["2026-01-01","2026-08-31"]}}' | head -c 800
curl -s -o /dev/null -w 'undeclared_metric=%{http_code}\n' -X POST localhost:38200/v1/query -H 'Authorization: Bearer <tenant_a>' -d '{"metric":"not_a_metric"}'
curl -s -w '\ncross_tenant=%{http_code}\n' -X POST localhost:38200/v1/query -H 'Authorization: Bearer <tenant_a>' -d '{"metric":"revenue_net","filters":{"tenant_id":"tenant_b"}}'
curl -s -o /dev/null -w 'alg_none=%{http_code}\n' -X POST localhost:38200/v1/query -H 'Authorization: Bearer <alg-none token>' -d '{"metric":"revenue_net"}'
curl -s localhost:38200/metrics | grep -E 'slot_lag|rows_total|last_success|failure' | head
docker compose -p odoo19-bct exec -T warehouse-db psql -U warehouse -d warehouse -c "select * from warehouse.pipeline_state order by last_success_at desc limit 10;"
```

## Escalation triggers — stop and return to Lead
- `warehouse.column_policy` is missing a column you must extract. **Hard-fail; do not default it.**
- The HMAC spec from `custom_pdp_masking` cannot be reproduced identically in Python.
- Meeting the ADR's 60 s PPOB SLA is not achievable on this hardware — report measured lag rather
  than quietly missing it.
- You need to edit `analytics/dbt/**`, `analytics/warehouse/**`, `addons/**` or `ci.yml`.
- Any design that would require the browser to hold a token that can reach the database directly.
