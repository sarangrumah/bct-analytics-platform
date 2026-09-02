# Contract 6 — API (Backend → Frontend, DWH, Security)

Status: **PUBLISHED at end of Phase 3.** Producer: Backend (`semantic-api`, `login-gateway`).
Consumers: `insight-portal` (Frontend), Data Warehouse (Grafana panels), Security, QA.

Everything below was produced by running against the live stack. Where this document shows output,
that output is verbatim.

---

## 1. Services and ports

| Service | Container | Host | In-network |
|---|---|---|---|
| `login-gateway` | `odoo19-bct-login-gateway` | `127.0.0.1:38120` | `odoo19-bct-login-gateway:8080` |
| `semantic-api` | `odoo19-bct-semantic-api` | `127.0.0.1:38200` | `odoo19-bct-semantic-api:8080` |
| CDC consumer | `odoo19-bct-cdc` | not published | `odoo19-bct-cdc:9108` (metrics only) |

Both HTTP services: non-root (uid 10002 / 10003), `no-new-privileges`, `cap_drop: ALL`, read-only
root filesystem, base image `python:3.12-slim@sha256:09f7da3b…` with openssl patched to
`3.5.7-1~deb13u2`.

**`semantic-api` holds exactly one database DSN, to the warehouse, as `warehouse_rls`.** It has no
route to Odoo's OLTP Postgres, so anti-pattern §7.3 is prevented structurally, not by policy.

---

## 2. `POST /v1/query` — the only data endpoint

### Request

```json
{
  "metric": "revenue_net",
  "dimensions": ["date_month"],
  "filters": {"date_range": ["2026-01-01", "2026-08-31"]},
  "order_by": "-value",
  "limit": 500
}
```

| Field | Type | Rules |
|---|---|---|
| `metric` | string, required | must exist in the registry, else `400` |
| `dimensions` | string[] | each must be in that metric's `dimensions`, else `400`. No duplicates |
| `filters` | object | each key must be in that metric's `filters`; required filters must be present |
| `order_by` | string, optional | `value` or a **requested** dimension; `-` prefix descends |
| `limit` | integer, optional | default 1000, capped at `SEMANTIC_API_MAX_LIMIT` (5000) |

**There is no field that carries SQL, and no code path that compiles a caller string into an
identifier.** Every identifier is looked up in the metric definition first; every value is a bound
parameter. An injection payload is therefore rejected by the allow-list *before any SQL exists* —
it is not escaped-and-executed-safely.

**`tenant_id` is never read from the request.** It comes only from the verified JWT. A `tenant_id`
in `filters` that differs from the session's is a scope violation (`403`), never an override.

### Response — 200

```json
{
  "metric": "revenue_net",
  "dimensions": ["date_month"],
  "rows": [
    {"date_month": "2026-01-01", "value": 44170500.0},
    {"date_month": "2026-02-01", "value": 44279500.0}
  ],
  "meta": {
    "tenant_id": "bct",
    "row_count": 8,
    "last_refreshed_at": "2026-08-31T01:13:57.493101+00:00",
    "is_stale": false,
    "refresh_sla_seconds": 900,
    "source_model": "mart_revenue_daily",
    "unit": "IDR",
    "type": "decimal",
    "query_duration_ms": 59.2
  }
}
```

Every row is `{<each requested dimension>: value, "value": <the measure>}`. The measure is always
keyed `value`, whatever the metric — so a chart component binds to one key rather than to a name it
has to look up.

### `meta.last_refreshed_at` and `meta.is_stale`

Read from **`warehouse.mart_freshness`**, DWH's view over `warehouse.pipeline_state` joined to
`warehouse.mart_sla`. Never from a clock — not the client's and not the API's. A service that
computed freshness from its own `now()` would report "fresh" whenever it had just restarted, which
is exactly when it knows least.

**A mart with no `pipeline_state` row reports `is_stale: true`.** Unknown freshness is not fresh
freshness. If the lookup itself fails, the response still carries `is_stale: true` plus a `note`.

### Error responses

| Status | Body | When |
|---|---|---|
| `400` | `{"error":"unknown_metric","detail":…,"field":"metric","available":[…]}` | metric not in the registry |
| `400` | `{"error":"invalid_query","detail":…,"field":"dimensions"\|"filters"\|"order_by"\|"limit"}` | anything undeclared or mistyped |
| `401` | `{"error":"unauthorized","detail":"Invalid token."}` | missing, malformed, expired, wrong `alg`, unknown `kid`, bad signature |
| `403` | `{"error":"tenant_scope_violation","detail":"Session is not scoped to the requested tenant."}` | cross-tenant request — **verbatim from contract 02** |
| `503` | `{"error":"scope_guard","detail":"Request refused for safety; retry."}` | the T-1 pool guard tripped |
| `503` | `{"error":"overloaded","detail":…,"retry_after":1}` + `Retry-After: 1` | **saturation** — every warehouse connection busy past the acquire timeout |

The `401` body is identical for every cause. Distinguishing them turns the endpoint into an oracle.
The `403` body never reveals whether the other tenant exists.

### Saturation — what the service does when every connection is busy

Added after Frontend, measuring p95, found this failure mode was **undocumented**. A ten-panel PPOB
view with its cache disabled issued ten concurrent queries against a pool of eight; psycopg2 raised
`PoolError`, it fell through to the generic handler, and **133 requests in a 300-request run
returned `500 query_failed`**. Reproduced by Backend at 52/300. `bct_semantic_pool_guard_trips`
stayed at `0` throughout, so this was never the T-1 guard — the two `503`s above are unrelated
conditions that happen to share a status code.

**The behaviour is queue, then shed, in that order.** Neither alone is correct:

* **Queue** — a request that finds every connection busy waits up to
  `SEMANTIC_API_POOL_ACQUIRE_TIMEOUT_MS` (default **2000 ms**). Queries against these marts take
  milliseconds, so a burst of ten panels against a pool of sixteen clears without any caller seeing
  an error. Shedding immediately would turn a 15 ms burst into user-visible failures.
* **Shed** — past that timeout the request is refused with `503 overloaded` and a `Retry-After`
  header. Queueing without a bound is how a saturated service becomes a hung one: latency grows
  without limit, callers time out anyway, and their retries make it worse.

**A `500` is never the correct response to saturation.** `500` tells a caller "this server is
broken, stop"; the truth is "every connection is busy, come back". Clients behave differently under
those two contracts, and the undocumented `500` is why Frontend had to spend time establishing
whether the bug was its own.

`SEMANTIC_API_POOL_MAX` defaults to **16**, derived from the warehouse's budget rather than chosen
to make a symptom disappear: `max_connections` 40 − 3 `superuser_reserved_connections` = 37 usable,
less 5 for a dbt build (`DBT_THREADS + 1`, **measured** by DWH through a full build; total peak
concurrency was 10), ~3 for `postgres_exporter` (**unverified** -- nothing pins it, and it connects
as `warehouse_rls`, the same role this pool uses. DWH's `c5094db` gave it
`application_name=warehouse-exporter`, so it is now **measurable but still not measured** — the
means existing is not the measurement being taken), 3 for the CDC loader (structural), ~4 for
operator/ad-hoc psql
and margin. Raising it moves the cliff; it does not remove one. **The fix is that exceeding the
ceiling now degrades correctly at any concurrency**, and slack being larger than first estimated is
not a reason to grow the pool. `analytics/warehouse/bin/warehouse_ctl.py verify` checks the total
against the live `max_connections` and names each claimant, so oversubscription is caught rather
than discovered as a 503.

Clients should honour `Retry-After` rather than retrying immediately. A client-side concurrency cap
(Frontend caps itself at four in flight) is a good neighbour policy, **not** a substitute for this:
any other caller — a second tab, the export path, a load test — gets the documented `503`.

Observability: `bct_semantic_pool_waits_total` (requests that queued and then succeeded — non-zero
is healthy), `bct_semantic_pool_shed_total` (requests refused with `503 overloaded`) and
`bct_semantic_pool_max_connections` (the ceiling those two are relative to).

---

## 3. `GET /v1/metrics` — the catalogue

Requires a valid token; the catalogue is not public. Returns every metric's `name`, `label`,
`description`, `grain`, `dimensions`, `filters`, `type`, `unit`, `aggregation`,
`refresh_sla_seconds` and `pdp_class`. **Frontend should build its query UI from this**, not from a
hardcoded list — adding a dimension is backwards-compatible and will appear automatically.

## 4. `GET /healthz` and `GET /metrics`

`/metrics` → Prometheus text format. Neither endpoint requires a token; neither exposes data.

`/healthz` probes the **warehouse**, not just the process:

| Status | Body | Meaning |
|---|---|---|
| `200` | `{"status":"ok","registry_metrics":11,"warehouse":"ok"}` | serving |
| `200` | `{"status":"degraded",…,"warehouse":"saturated"}` | pool saturated; the database is fine and the service is still serving |
| `503` | `{"status":"down",…,"warehouse":"unreachable"}` | the warehouse cannot be reached |

It previously returned `{"status":"ok","metrics":N}` after counting registry entries, which are
read from a YAML file at import — so it reported healthy whenever the **process** was alive. That
was found live rather than by review: after the warehouse container was destroyed, a semantic-api
left over from a previous session answered **`200 {"status":"ok"}` while every `/v1/query`
returned `500`**, on the port a cold start needed. Anything probing `/healthz` would have taken a
green from a service incapable of answering a single question.

`degraded` and `down` are deliberately distinct. A saturated pool means the database is healthy
and the service is serving; reporting it as down would pull an instance out of rotation at exactly
the moment it is busiest — the same 500-vs-503 conflation described in §2, one layer up.

**Consumers should treat a `200` from `/healthz` as meaning the warehouse answered**, which is the
only reading that makes the endpoint worth probing.

---

## 5. Session — contract 02, as implemented

### `POST /auth/login`

```json
{"db": "bct", "login": "…", "password": "…"}
```

Response:

```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsImtpZCI6IlpKaDRQOU42…",
  "token_type": "Bearer",
  "expires_in": 3600,
  "expires_at": "2026-08-31T02:13:46+00:00",
  "kid": "ZJh4P9N6alKDX9On",
  "tenant_id": "bct",
  "roles": ["analytics.admin", "analytics.analyst", "analytics.viewer"],
  "allowed_ou": [],
  "all_ou": true
}
```

Plus a refresh cookie: `httpOnly`, `Secure`, `SameSite=Strict`, `Path=/auth`.

### Claim set (verbatim from a live token)

```json
{
  "iss": "https://login-gateway.local/",
  "aud": "insight-portal",
  "sub": "odoo:bct:2",
  "tenant_id": "bct",
  "odoo_uid": 2,
  "roles": ["analytics.admin", "analytics.analyst", "analytics.viewer"],
  "allowed_ou": [],
  "all_ou": true,
  "company_ids": [1],
  "iat": 1788138826,
  "exp": 1788142426
}
```

### `allowed_ou` and `all_ou` — read this carefully

Per ruling `a0fbb88`:

- **`all_ou: true`** — the user holds `custom_operating_unit.group_operating_unit_all`, the explicit
  record-rule bypass. No Operating Unit predicate is applied.
- **`all_ou: false`** with a non-empty `allowed_ou` — restricted to those ids.
- **`all_ou: false`** with `allowed_ou: []` — **no Operating Units**, mirroring Odoo's record rules,
  which fail closed on an empty entitlement. The compiler emits `operating_unit_id = -1`.
- **`all_ou` absent** — treated as `false`. The bypass must be explicit, so a token that predates
  the claim grants nothing rather than everything.

> **`operating_unit_id = -1` is the UNASSIGNED dimension member, not a missing value.** Verified: no
> mart row carries a NULL `operating_unit_id`. An earlier implementation mapped the empty
> entitlement to `IS NULL` by analogy with Odoo and would have matched **nothing at all** — an
> unentitled user would have seen an empty dashboard forever instead of the unassigned rows they are
> entitled to. Frontend does not need to handle this; it is applied server-side.

### `POST /auth/refresh`, `POST /auth/logout`

Refresh tokens are **opaque and single-use**, not JWTs — a self-contained refresh token cannot be
revoked, which makes logout a lie. Each refresh rotates the token and **re-reads entitlements from
Odoo**, so a revoked Operating Unit or role does not stay effective for the fortnight a session
lasts.

### JWKS — `GET /.well-known/jwks.json`

`http://odoo19-bct-login-gateway:8080/.well-known/jwks.json` (in-network),
`http://127.0.0.1:38120/.well-known/jwks.json` (host).

**Two keys are published from day one (finding T-4).** Verbatim:

```json
{"keys":[
  {"kty":"RSA","use":"sig","alg":"RS256","kid":"ZJh4P9N6alKDX9On","n":"woWvyHKuJQxXu_k3…","e":"AQAB","x-bct-status":"active"},
  {"kty":"RSA","use":"sig","alg":"RS256","kid":"ftbQONlr2eKtPN04","n":"kO2I1wpHygXUD4fw…","e":"AQAB","x-bct-status":"standby"}
]}
```

A one-key JWKS cannot be rotated without an outage: publish the new key first and verifiers holding
a cached JWKS reject every token; sign with it first and they reject every token until they refetch.
With both published and `kid` selecting between them, rotation is:

1. both keys are already in JWKS and every verifier already accepts both;
2. flip `LOGIN_GATEWAY_JWT_KID` to the standby kid and restart the gateway;
3. tokens signed by the old key keep verifying until they expire (3600 s);
4. mint a fresh standby and repeat.

`kid` is derived from the key — the first 16 characters of the base64url SHA-256 of the DER public
key — so a `kid` can never name a key that is not the one loaded. `x-bct-status` is informational
only; **select by `kid`, never by that field.**

### Verification rules for any verifier

Algorithm pinned to **RS256**; `alg: none` and HS256 confusion rejected before key selection.
`iss` and `aud` checked exactly, `exp`/`nbf` with 30 s leeway. A token with no `kid` is rejected —
`kid` selection is what makes rotation work.

---

## 6. Prometheus metric names

Agreed with the Data Warehouse agent through the Lead. **Renaming one breaks a Grafana panel
silently — treat these as contract, not as log lines.**

### CDC consumer — `odoo19-bct-cdc:9108`

| Metric | Type | Labels |
|---|---|---|
| `bct_cdc_rows_total` | counter | `tenant`, `source_table`, `op` (`I`/`U`/`D`) |
| `bct_cdc_end_to_end_lag_seconds` | gauge | `tenant`, `source_table` |
| `bct_cdc_replication_slot_lag_bytes` | gauge | `tenant`, `slot` |
| `bct_cdc_slot_invalidated` | gauge | `tenant`, `slot` — `1` when `wal_status='lost'` |
| `bct_cdc_last_success_timestamp_seconds` | gauge | `tenant`, `source_table` |
| `bct_cdc_failure_count_total` | counter | `tenant`, `source_table` |
| `bct_cdc_backfill_progress_ratio` | gauge | `tenant`, `source_table` |
| `bct_cdc_up` | gauge | `tenant` |
| `bct_cdc_landing_row_amplification` | gauge | `tenant`, `source_table` |
| `bct_cdc_landing_duplicate_changes` | gauge | `tenant`, `source_table` |

Notes that matter for panel design:

- **There is no `rows_per_second`.** Derive throughput as `rate(bct_cdc_rows_total[5m])` and state
  the window in the legend, so the averaging window is visible to whoever reads the panel.
- **`bct_cdc_last_success_timestamp_seconds` is a HEARTBEAT**, advanced on a 15 s timer independent
  of message arrival, including cycles that move zero rows. It has to be: it backs `meta.is_stale`,
  PPOB's SLA is 60 s, and if it only moved when rows flowed then one quiet minute would make every
  PPOB mart report itself stale.
- **`bct_cdc_replication_slot_lag_bytes` is the consumer's view and goes ABSENT, not high, when the
  consumer dies** — which is the failure it would most need to report. **Page on
  `postgres_exporter`'s `pg_replication_slots_pg_wal_lsn_diff` instead**, which keeps reporting after
  this process is gone. The value of publishing both is that they can *disagree*.

  > **A trap when verifying this, worth knowing before you conclude the series is missing.** These
  > are **per-slot** series: with no replication slot in existence, `postgres_exporter` emits none of
  > them, and "no samples" is indistinguishable from "this exporter does not export them". Both QA
  > and the Lead measured zero during the window when a cold start had destroyed every slot, and
  > concluded the paging path did not exist. It does. Verified directly with a slot present:
  >
  > ```
  > pg_replication_slots_pg_wal_lsn_diff{slot_name="bct_slot_bct",...} 56
  > pg_replication_slots_active{slot_name="bct_slot_bct",...} 1
  > pg_replication_slot_wal_status{slot_name="bct_slot_bct",wal_status="reserved"} 1
  > ```
  >
  > v0.16 emits both the built-in `pg_replication_slot_slot_*` family and the legacy
  > `pg_replication_slots_*` names, so no rule expression needs changing. **Establish that a slot
  > exists before reading an absence as evidence** — an alert test should skip with a reason when
  > there are none, not fail, and not pass.

### semantic-api — `odoo19-bct-semantic-api:8080/metrics`

| Metric | Type | Labels |
|---|---|---|
| `bct_semantic_query_total` | counter | `metric`, `status` |
| `bct_semantic_query_duration_seconds` | histogram | `metric` |
| `bct_semantic_stale_response_total` | counter | `metric` |
| `bct_semantic_tenant_scope_violation_total` | counter | — |
| `bct_semantic_pool_guard_trips` | gauge | — |

Histogram buckets bracket the §4 p95 budget of 2 s:
`0.05, 0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 10`.

`bct_semantic_pool_guard_trips` should be `0` forever. Non-zero means the T-1 `SET LOCAL` discipline
was bypassed and requests were refused; alert on **any** increase.

### login-gateway — `odoo19-bct-login-gateway:8080/metrics`

| Metric | Type | Labels |
|---|---|---|
| `bct_gateway_auth_total` | counter | `result` (`success`/`invalid`/`ratelimited`/`upstream_error`) |
| `bct_gateway_token_issued_total` | counter | `tenant` |
| `bct_gateway_jwks_keys` | gauge | — |

`bct_gateway_jwks_keys < 2` means the deployment has lost its rotation story (T-4). Alert on it.

---

## 7. Fixtures for Frontend

```
python scripts/analytics/metric-fixtures.py                 # offline shapes, no warehouse needed
SEMANTIC_API_TOKEN=<token> python scripts/analytics/metric-fixtures.py   # live transcripts
```

Output lands in **`analytics/semantic-api/metrics/fixtures/`**: one `<metric>.json` per metric in
the exact `/v1/query` envelope, plus `_catalogue.json` in the `/v1/metrics` envelope.

Generated from the same registry the API serves. **Hand-writing a fixture shape is a brief
violation (§2.4)** — if a metric changes, regenerate, and Frontend finds out at build time.

> The brief specifies `make metric-fixtures`. The `Makefile` is Platform-Infra's file and
> `metric-fixtures` is not in Backend's reserved target list, so the script is published here and a
> one-line target has been requested from the Lead rather than added unilaterally.

---

## 8. Metrics currently defined

| Metric | Source model | Measure | Unit | SLA |
|---|---|---|---|---|
| `revenue_net` | `mart_revenue_daily` | `revenue_net` | IDR | 900 s |
| `sales_total` | `mart_sales_daily` | `amount_total` | IDR | 300 s |
| `sales_untaxed` | `mart_sales_daily` | `amount_untaxed` | IDR | 300 s |
| `ppob_transaction_count` | `mart_ppob_transaction` | `transaction_count` | — | 60 s |
| `ppob_sla_breach_count` | `mart_ppob_transaction` | `sla_breach_count` | — | 60 s |
| `ppob_commission_revenue` | `mart_ppob_transaction` | `commission_revenue` | IDR | 60 s |
| `stock_net_quantity` | `mart_stock_position` | `net_qty` | unit | 300 s |

Three constraints the registry enforces at load time, so a bad metric fails the build rather than
returning a wrong number:

1. **PPOB revenue is `commission_revenue`, never `pass_through_amount`.** The pass-through is money
   owed to the biller — not ours, not revenue. Measured on live data, binding it would overstate
   revenue by **481×**. Any metric summing a `pass_through_*` column as IDR is refused.
2. **`mart_revenue_daily` UNIONs three channels** (`invoice`, `pos`, `ppob_commission`) rather than
   summing them, so any metric reading it must declare `channel_note` stating that summing across
   channels is intended. Credit notes are already netted off inside `invoice`.
3. **`stock_net_quantity` has no date filter.** `mart_stock_position` is a position, not a daily
   series. Declaring a `date_range` would have produced a metric that fails at query time.

`date_month` is a **derived** dimension (`date_trunc('month', date_day)::date`) because no mart
carries that column. The base column and the grain both come from the version-controlled metric
file and the grain is validated against an enumerated set, so no free-form SQL exists anywhere.

---

## 9. Environment variables added by Backend

Added per contract 04 §5's procedure (extend, never rename; `changeme` in `.env.example`):

```
LOGIN_GATEWAY_JWT_NEXT_KID                 LOGIN_GATEWAY_JWT_NEXT_PRIVATE_KEY_PATH
LOGIN_GATEWAY_JWT_NEXT_PUBLIC_KEY_PATH     LOGIN_GATEWAY_REFRESH_TOKEN_TTL
LOGIN_GATEWAY_ODOO_URL                     LOGIN_GATEWAY_ALLOWED_DATABASES
LOGIN_GATEWAY_COOKIE_SECURE                LOGIN_GATEWAY_RATE_LIMIT_MAX_ATTEMPTS
LOGIN_GATEWAY_RATE_LIMIT_WINDOW_SECONDS    LOGIN_GATEWAY_RATE_LIMIT_LOCKOUT_SECONDS
SEMANTIC_API_HOST_PORT                     SEMANTIC_API_JWKS_URL
SEMANTIC_API_JWT_ISSUER                    SEMANTIC_API_JWT_AUDIENCE
SEMANTIC_API_WAREHOUSE_HOST                SEMANTIC_API_WAREHOUSE_PORT
SEMANTIC_API_MAX_LIMIT                     SEMANTIC_API_POOL_MAX
SEMANTIC_API_POOL_ACQUIRE_TIMEOUT_MS
```

`SEMANTIC_API_POOL_MAX` (default 16) and `SEMANTIC_API_POOL_ACQUIRE_TIMEOUT_MS` (default 2000) both
have working defaults in code, so an existing `.env` needs no edit — see §2 "Saturation".

Signing keys are generated by `scripts/analytics/gen-jwt-keys.sh` into `login-gateway/secrets/`,
which is gitignored, and mounted read-only at `/run/secrets`. **Private keys are never baked into an
image** — a key in a layer is a key in every registry copy and every `docker save` tarball.

---

## 10. What Backend guarantees, and what it deliberately cannot do

- The API **never** accepts SQL, and no code path turns a caller string into an identifier.
- `tenant_id` comes **only** from the verified JWT — never a header, query string, cookie or body.
- Isolation is enforced by Postgres RLS with `app.tenant_id` set via `SET LOCAL` inside an explicit
  transaction, **and** by a bound `tenant_id` predicate. A mistake in either shows up as an empty
  result, not as a leak.
- The API performs **no masking and can perform none**: the data is already masked upstream, there
  is no salt in the process, and no unmasking function exists anywhere in the codebase.
- The browser never receives a connection string, never receives a token that reaches the database,
  and never receives more rows than `limit` allows.
