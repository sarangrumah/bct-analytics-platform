# Architecture — what was actually built

This describes the system as it exists in this repository, not as it was planned. Every component
named here has a file behind it; every number was measured on the running `odoo19-bct` stack. Where
something in the original plan does not exist, this document says so rather than omitting it.

The design decision behind the whole data path is `docs/adr/0001-analytics-warehouse.md`, accepted
at GATE 2. The seams between components are frozen in `docs/agents/contracts/01..06`.

---

## 1. The data path, from Odoo's WAL to a dashboard pixel

```
      ┌──────────────────────────────────────────────────────────────────────────────┐
      │  odoo19-bct-odoo            Odoo 19 CE, 5 custom addons                       │
      │    custom_pdp_core        the classification registry (698 columns)           │
      │    custom_pdp_masking     the HMAC spec + in-UI and export masking            │
      │    custom_operating_unit  OU record rules, fail-closed                        │
      │    custom_ppob            the PPOB domain                                     │
      │    custom_demo_seed       12 months of data across 2 OUs                      │
      └───────────────┬──────────────────────────────────────────────────────────────┘
                      │  ORM writes
                      ▼
      ┌──────────────────────────────────────────────────────────────────────────────┐
      │  odoo19-bct-postgres        Postgres 16, wal_level = logical                  │
      │                             max_slot_wal_keep_size = 2GB                      │
      │   PUBLICATION bct_cdc_<slug>   ── the tables that are decoded                 │
      │   SLOT        bct_slot_<slug>  ── Postgres's bookmark; RETAINS WAL            │
      └───────────────┬──────────────────────────────────────────────────────────────┘
                      │  pgoutput logical decoding, over a replication connection
                      │  as warehouse_reader  (SELECT + REPLICATION, nothing else)
                      ▼
      ┌──────────────────────────────────────────────────────────────────────────────┐
      │  odoo19-bct-cdc             analytics/cdc/bct_cdc                             │
      │    1. read warehouse.column_policy  -> what to select, what to mask           │
      │    2. backfill: keyset-paged snapshot, resumable from max(id) in raw          │
      │    3. stream:   decode I/U/D, MASK IN FLIGHT, append to raw.*                 │
      │    4. confirm the LSN only AFTER the warehouse transaction commits            │
      └───────────────┬──────────────────────────────────────────────────────────────┘
                      │  INSERT only, as warehouse_loader
                      ▼
      ┌──────────────────────────────────────────────────────────────────────────────┐
      │  odoo19-bct-warehouse-db    Postgres 16                                       │
      │                                                                              │
      │   raw.*        append-only landing zone, 15 tables                            │
      │                a DELETE is a tombstone row (_op='D'), never a removal         │
      │        │                                                                     │
      │        │  dbt (as `warehouse`)                                               │
      │        ▼                                                                     │
      │   staging.*    stg_ models: the latest-non-deleted version per key            │
      │        │                                                                     │
      │        ▼                                                                     │
      │   marts.*      16 facts and dimensions, every one carrying tenant_id,         │
      │                every one ENABLE + FORCE ROW LEVEL SECURITY                    │
      │                                                                              │
      │   warehouse.*  column_policy · pipeline_state · mart_sla · mart_freshness     │
      │                tenant_registry · log_access() · access_audit                  │
      └───────────────┬──────────────────────────────────────────────────────────────┘
                      │  SELECT under RLS, as warehouse_rls, with app.tenant_id
                      │  set by SET LOCAL inside an explicit transaction
                      ▼
      ┌──────────────────────────────────────────────────────────────────────────────┐
      │  odoo19-bct-semantic-api    POST /v1/query — no raw SQL is ever accepted      │
      │    compiles {metric, dimensions, filters} from the metric contract            │
      │    tenant_id comes from the verified JWT, never a header or body              │
      │    every response carries meta.last_refreshed_at / meta.is_stale              │
      └───────────────┬──────────────────────────────────────────────────────────────┘
                      │  RS256 JWT, verified against JWKS, algorithm pinned
                      ▼
      ┌──────────────────────────────────────────────────────────────────────────────┐
      │  odoo19-bct-insight-portal  Next.js. Server-side only holds the token.        │
      │  odoo19-bct-login-gateway   authenticates against Odoo over JSON-RPC,         │
      │                             issues the RS256 JWT, publishes JWKS              │
      └──────────────────────────────────────────────────────────────────────────────┘
```

Measured latency for one record travelling the first four boxes, on this stack:

```
CREATE 0.16s | UPDATE 0.15s | DELETE 0.20s        budget 60s each
```

---

## 2. Why logical decoding rather than a `write_date` tap

Odoo carries `write_date` on nearly every table, which makes an incremental tap tempting. It is
wrong here for one reason that no amount of care fixes: **`unlink()` leaves no trace.** Neither does
`ON DELETE CASCADE`, nor a direct SQL write that bypasses the ORM. A `write_date` tap therefore
drifts, and nothing reports the drift — the warehouse simply keeps returning a row that no longer
exists.

Logical decoding sees every `INSERT`, `UPDATE` **and `DELETE`** at the WAL level. That is the whole
argument, and it is why `tests/test_01_live_sync.py` exists and why its delete leg is the one that
matters: if a delete does not disappear from the mart, the architecture's central claim is false.

Debezium was rejected under anti-pattern §7.6 — it needs Kafka to be useful, and that footprint is
not justifiable next to Odoo on one VPS.

---

## 3. The five properties that are structural rather than policy

Each of these is enforced by something that cannot be forgotten, and each has a test that would fail
if it regressed.

### 3.1 The warehouse cannot write to Odoo

`warehouse_reader` holds `SELECT` and `REPLICATION` and nothing else. There is no write path from
analytics into the ERP — not because a rule forbids it, but because the role cannot. Verified with
pasted denials for `INSERT`, `UPDATE`, `DELETE`, `CREATE TABLE` and `CREATE TEMP TABLE`.

### 3.2 The landing zone is append-only

`warehouse_loader` holds `SELECT` + `INSERT` on `raw.*` and **no `UPDATE`, no `DELETE`, no
`TRUNCATE`**. A change is a new row; a delete is a tombstone. The rule is enforced by the grant
rather than trusted to the loader's code.

### 3.3 Unclassified data cannot land

`warehouse_loader` holds no `CREATE` on schema `raw` (`permission denied for schema raw`). The DDL is
generated from `warehouse.column_policy`, so a column with no classification has nowhere to go. A
missing landing table is a schema-drift signal to escalate, not something the loader may fix.

### 3.4 Masking happens during load, not after

The digest is applied in the loader, before the `INSERT`. There is no window in which the warehouse
holds a personal value in cleartext, and no cleanup job that could fail to run.

### 3.5 Tenant isolation is enforced by the storage engine

Every mart carries `ENABLE` **and `FORCE`** row-level security, applied by a dbt `post-hook` so it
survives `--full-refresh` (which drops and recreates the table, taking any hand-made policy with it).
The serving role is `NOSUPERUSER NOBYPASSRLS`, so the policies are actually evaluated for it.

Measured: **13,755 rows belonging to `bct_t2` exist across the marts; a session scoped to `bct` saw
none of them.**

---

## 4. The four warehouse roles, and why there are four

A single shared identity would make every isolation test in this project pass and mean nothing,
because Postgres never evaluates RLS for a `SUPERUSER` or a `BYPASSRLS` role.

| Role | Attributes | Used by | May |
|---|---|---|---|
| `warehouse_admin` | **SUPERUSER** | init DDL, backup | everything. **Nothing queries data as this role.** |
| `warehouse` | `NOSUPERUSER NOBYPASSRLS`, owns the schemas | dbt | build models; read all tenants *only while `app.tenant_id` is unset* |
| `warehouse_loader` | `NOSUPERUSER NOBYPASSRLS` | the CDC loader | `INSERT`+`SELECT` on `raw.*`; no `CREATE`, no `UPDATE`, no `DELETE` |
| `warehouse_rls` | `NOSUPERUSER NOBYPASSRLS` | semantic-api, exporter | `SELECT` on `marts.*` **under RLS**; no access to `raw` at all |

Two consequences worth stating because they are easy to miss:

- **`warehouse` is not exempt from RLS just because it owns the tables.** `FORCE ROW LEVEL SECURITY`
  is what subjects an owner to its own policies. Its unscoped read is a *policy*
  (`p_transform_unscoped`) that applies only while `app.tenant_id` is unset, so the instant anyone
  sets that variable — a human included — only the tenant policy remains.
- **A role with no privilege on a schema sees its tables as absent, not inaccessible.** An empty
  `\dt` is ambiguous between "the DDL never ran" and "you cannot see it", and those have completely
  different fixes.

---

## 5. Freshness is read, never assumed

`warehouse.pipeline_state` is the only source of `meta.last_refreshed_at` and `meta.is_stale`. The
loader advances `last_success_at` from a heartbeat on a 15 s timer **in its own thread**, so it moves
on an idle pipeline as well as a busy one — an idle pipeline and a dead one would otherwise be
indistinguishable.

`warehouse.mart_freshness` computes `is_stale` against `warehouse.mart_sla`, which is ADR 0001's
freshness table as data. A mart with no `pipeline_state` row reports `is_stale = true`; it is never
"fresh" by default.

| Mart | SLA | On breach |
|---|---|---|
| `mart_ppob_transaction`, `fct_ppob_transaction` | 60 s | **page** |
| `mart_sales_daily`, `mart_stock_position`, `fct_sale_order_line`, `fct_stock_move`, `fct_pos_order_line` | 300 s | alert |
| `mart_revenue_daily` | 900 s | alert |
| `mart_account_move_line`, `fct_account_move_line`, `dim_*` | 3600 s | alert |

The decisive test is not that it advances but that it **stops**. Measured: with the loader running,
`last_success_at` advanced within 2.3 s; stopped, it was byte-identical 35 s later; restarted, it
advanced again within 2.3 s. Anything driven by a clock passes the first and fails the second.

---

## 6. The replication slot is the sharp edge

A slot retains WAL for as long as it exists. An unconsumed slot therefore fills the disk, and a full
disk **takes Odoo down** — a warehouse outage becoming an ERP outage, which is anti-pattern §7.9.

`max_slot_wal_keep_size = 2GB` makes Postgres drop the slot instead. That is a deliberate trade:
the warehouse is re-seedable, the ERP is not. Its consequence is that the alerting is load-bearing
rather than decorative:

| Alert | Threshold | `for:` |
|---|---|---|
| `ReplicationSlotWalRetentionWarning` | 512 MiB retained (25% of cap) | 10m |
| `ReplicationSlotWalRetentionCritical` | 1 GiB retained (50% of cap) | 5m |
| `ReplicationSlotInvalidated` | `wal_status="lost"` | 1m |
| `ReplicationSlotInactive` | no consumer | 15m |

**NOT PROVEN, and recorded as such:** that this alerting is live after a cold start. The overlay is
not brought up by `make up-dev` or `make up-analytics`, and the check that would confirm it
(`make check-alerting`) now works and is verified able to fail, but has not yet run inside a
cold-start execution. See
`docs/runbooks/analytics-pipeline.md` §3.4 for the manual verification and for the command that will
prove it once the fix lands.

The loader treats an invalidation as **fatal** and exits non-zero rather than reconnecting.
Reconnecting would resume from a later position and leave a hole in the mart with no error anywhere:
the failure mode that looks like success.

Recovery is `docs/runbooks/analytics-pipeline.md` §3.

---

## 7. Session and API

`login-gateway` authenticates against Odoo over JSON-RPC and issues an **RS256** JWT. It holds the
private key; verifiers fetch only the public half from JWKS and therefore never hold signing
material. Two keys are published with distinct `kid`s and **distinct moduli** — a rotation that
changed only the `kid` would leave a compromised key valid under a new name.

`semantic-api` exposes exactly one data endpoint, `POST /v1/query`, which compiles a query from the
metric contract. **It never accepts SQL.** `tenant_id` comes from the verified token and from
nowhere else; a session for tenant A asking for tenant B receives:

```
HTTP 403
{"error":"tenant_scope_violation","detail":"Session is not scoped to the requested tenant."}
```

byte-identical for a tenant that exists and one that does not, so the error leaks no tenant
existence.

`allowed_ou: []` means **no** Operating Units — matching `custom_operating_unit`'s fail-closed record
rules — and `all_ou` is a separate, explicit boolean. Absent `all_ou` is `false`. This reverses the
contract as originally frozen; the original wording would have shown a user with no entitlement
*more* in the dashboard than in Odoo, which is a privilege escalation manufactured by two documents
disagreeing.

---

## 8. What does not exist

> **Read §8.1 first.** On 2026-09-01 this section was found to be stale in two places, both
> now struck through below, and the platform gained a four-stack layout this document does
> not yet describe end to end. What follows is accurate as corrected; §8.1 records what is
> still genuinely absent.

### 8.1 The ATHERA build, and what is genuinely left

This section listed six absent surfaces on 2026-09-01. All six were built on 2026-09-01 and
2026-09-02. It is rewritten rather than deleted, because a reader comparing the diagram to the
code needs to know which parts are load-bearing and which are stubs.

| Diagram node | Where it is |
|---|---|
| ATHERA Company Profile / Product / ETC. | `marketing-site/`, pages in `cms.page` |
| Login, Super Admin? | `login-gateway/`, claim `is_super_admin` |
| Super Admin CMS, Client Management | `hub-portal/` at `admin.athera.localhost` |
| Subscription Management, Active? | `tenant_registry.plans`, `is_active()`, claim `subscription_active` |
| Postgres (control plane) | schemas `tenant_registry` + `cms` in the admin Odoo database |
| Client Dashboard, ATHERA Insight | `insight-portal/` + `semantic-api/` + the warehouse |
| ODOO | the odoo stack, plus the delivery modules already installed |
| ATHERA Agent | `ai-gateway/` |
| Client Environment x2 | `bct` and `acme`, with different plans and different entitlements |
| Odoo Postgres -> DW | `analytics/cdc/` |
| Reverse proxy | `caddy/` |

**Genuinely left, and each is a decision rather than an oversight:**

- **The live Anthropic call has never run.** There is no API key on the build machine and the
  operator has chosen to leave it unset. Everything around the call is exercised: HMAC in both
  directions, the tenant fence (10 tests), the schema refusals, the 501s and the
  no-credential error path. The call itself is untested, and this says so rather than implying
  otherwise.
- **`/v1/workflow/anomaly` and `/v1/workflow/classify` answer 501.** `custom_ai_features` calls
  both. An anomaly scan that silently returned "no anomalies" would be believed.
- **Backups through the orchestrator answer 501.** `scripts/tenant-backup.sh` does it correctly
  on the host; that container has neither `pg_dump` nor the filestore, deliberately.
- **`gentle` is a registry row with no route.** A third tenant, provisioned end to end through
  the API as the orchestrator's acceptance test, deliberately left without a network alias or a
  Caddy block. Reachability is a separate, reviewed step -- see the `athera-client-onboard`
  skill.
- **Per-client dbt models and metrics for a non-Odoo Insight source.** `import-policy` classifies
  their columns; the marts and the metric registry are bespoke per client, which is the shape of
  the work rather than a defect in it.


### 8.2 Never built

Stated because a reader will otherwise assume the master prompt's world:

- **The 162-addon platform, the UU PDP module family, Coretax/e-Faktur and PPh withholding.** The
  operator chose greenfield with a 4+1 addon set on 2026-08-31. See PLAN.md's deviation record.
- **Keycloak.** Auth is `login-gateway` against Odoo over JSON-RPC.
- **`custom_pdp_audit`.** Warehouse access auditing is designed in contract 05 §B instead; consent
  and DSAR lifecycle are not modelled in Odoo at all.
- **Any DSAR erasure automation.** See `docs/pdp-compliance.md` §5 — it is a manual runbook, and
  that document says so in those words.
- ~~**`insight-portal`.**~~ CORRECTED 2026-09-01. It exists and it runs: Next.js 15, five views
  plus a drill-down under `/t/[tenant]/`, folded into `compose/insight.yml`. This entry said it was
  unbuilt for as long as it was, and then for a while after it was not — which is the failure mode
  this whole section exists to prevent, so it is struck through rather than deleted.
- ~~**The 162-addon platform.**~~ Also corrected: ADR 0002 imported 149 modules on 2026-09-01 and
  332 install. The line above is the decision as taken on 2026-08-31; the reversal is ADR 0002.
- **A workflow orchestrator.** ADR 0001 rejected Airflow and Dagster: the CDC consumer is a
  long-running process, and the only scheduled work is `dbt build` on an interval.

---

## 9. Where to look

| You want | Read |
|---|---|
| why Postgres and not ClickHouse | `docs/adr/0001-analytics-warehouse.md` |
| the seam between two agents | `docs/agents/contracts/0*.md` |
| something is broken | `docs/runbooks/analytics-pipeline.md` |
| the PDP position, including the erasure gap | `docs/pdp-compliance.md` |
| turning CI/CD on | `docs/cicd-activation.md` |
| shipping to the VPS | `docs/prod-deploy-checklist.md` |
| proof any of the above is true | `tests/`, and `bash tests/run.sh` |

---

## 10. §6 "Definition of done" — status, with the test that proves each item

Every item maps to a **named test** or to an explicit **not covered, because**. Statuses are the
result of the run described in the row, not a judgement. Anything unproven says **NOT PROVEN** in
those words; a §6 item recorded as unverified is worth more than one marked done on an assertion
that could not fail.

| §6 item | Status | Evidence |
|---|---|---|
| Live sync, create → update → **delete**, with timestamps | **PASS** | `test_01::test_live_sync_create_update_delete`. As `warehouse_loader` (`rolsuper=f, rolbypassrls=f`): create 0.18 s, update 0.24 s, delete 0.21 s against a 60 s budget; history `I→U→U→U→D`; the `_op='I'` row survives the update; latest-non-deleted returns 0 rows |
| …and the delete reaches the **mart** | **PASS** | `test_01::test_live_sync_delete_reaches_the_mart`. `dim_partner` row goes `is_current` `t`→`f` after `dbt build`; zero current rows, and no current row carries the digest |
| Cross-tenant access returns **403** | **PASS** | `test_06`, body asserted character-for-character against contract 02, and byte-identical for a tenant that does not exist. Re-proven on the cold-started stack with `bct_t2` at 311 rows *asserted first* |
| Reconciliation: warehouse totals == Odoo | **PASS** | `test_03`, 15/15 tables exact; debit = credit = 439,850,000.00 over 431 lines. dbt's own `assert_reconciliation_matches_odoo`: PASS=292, ERROR=0 |
| Masking: personal unreadable, `secret` absent | **PASS** | `test_04`. Digest re-derived by hand from `MODULE_KNOWLEDGE.md` rather than imported from the loader; five `secret` columns absent as *columns* |
| Tenant isolation enforced by the storage layer | **PASS** | `test_05`. 13,755 rows of `bct_t2` exist and none are visible to a `bct`-scoped `warehouse_rls` session; identity asserted before the isolation claim |
| Freshness is pipeline metadata, not a clock | **PASS** | `test_08`. Advances in 2.3 s, **freezes** byte-identically for 35 s with the loader stopped, resumes in 2.3 s. The freeze is the half a clock would fail |
| Idempotency | **PASS** | `test_02`. Second load over the same range: zero projection difference, zero rows appended |
| Slot-lag alert fires at the ADR thresholds | **PARTIAL** | `test_09` + `tests/prometheus/slot_lag_alerts_test.yml` via `promtool`, including the below-threshold negatives. Live firing **not** induced: it needs 512 MiB of retained WAL on a host carrying three other live stacks |
| Backfill resumability | **PASS**, not re-run | Killed at 5,545 rows, resumed from `id > 6706`, landed exactly 4,065, byte-identical checksum. Now **NOT RUN** on the current dataset: the largest table is 431 rows and has no middle to interrupt |
| Cold start from removed volumes | **PASS**, 9/11 | Volumes genuinely removed; `up-dev` → modules → credential → `up-analytics` → `seed-demo` → `up-gateway` → `up-semantic` → `cdc-start` → `dbt` → `up-obs` all green. Two failures traced to one ordering mistake **in the test**, fixed and demonstrated in both directions, **NOT PROVEN** end-to-end since the correction |
| Alerting live after a cold start | **PASS** | `test_11::test_the_observability_overlay_comes_back_and_alerting_is_live`, requiring exit 0 **and** no `SKIP`. `check-alerting: OK — 5/5 targets up, Alertmanager answering, 20/21 metrics with current samples` |
| Five dashboard views render | **not covered, because** the Grafana dashboard has never been opened in a browser and `insight-portal` view rendering is Frontend's to demonstrate. Panels are provisioned and their queries run |
| CD rollback demonstrated | **not covered by QA** — Security's, and demonstrated by them. CD has never executed against a real remote because there is no git remote |
