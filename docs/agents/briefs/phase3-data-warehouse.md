# Brief: Data Warehouse — Phase 3 (dimensional model, PDP policy, isolation, quality)

## Objective
A Postgres warehouse that a dashboard can trust: a star schema built by dbt from an append-only
landing zone, where personal data was already masked before it landed, where a tenant-scoped
connection is *provably incapable* of reading another tenant's rows, and where a mart that disagrees
with Odoo fails the pipeline loudly instead of quietly serving a wrong number.

## Read first — in this order
1. `docs/adr/0001-analytics-warehouse.md` — **Accepted at GATE 2 and binding on you.** Option A,
   Postgres marts, `pgoutput` logical replication, per-mart freshness table, 2 GB slot cap.
2. `docs/agents/contracts/01-classification.md` — the five PDP classes and their load-time transforms.
3. `docs/agents/contracts/03-metric.md` — the metric shape your marts must be able to answer.
4. `docs/agents/contracts/04-platform.md` — published by Platform-Infra: network/volume names,
   `warehouse_reader` connection shape, `.env` variable names, Makefile targets already taken,
   reserved ports.
5. `addons/custom_operating_unit/MODULE_KNOWLEDGE.md` — **required by master prompt §3.1** before you
   model `dim_operating_unit`. Also read `custom_pdp_core/MODULE_KNOWLEDGE.md` for the real
   classification map and `custom_ppob/` for the PPOB schema.

## Ground truth
Phase 1 delivered: Odoo 19 CE + Postgres 16 (`wal_level=logical`, `max_slot_wal_keep_size=2GB`,
`warehouse_reader` role with SELECT+REPLICATION only) under project `odoo19-bct`, plus five addons
including `custom_demo_seed` which generates 12 months of volume across ≥2 Operating Units.

**Do not stand up a second Odoo database to develop against** (master prompt §3.0, anti-pattern §7.1).
Develop against the Postgres already in `docker-compose.yml`.

## The seam with Backend — read this carefully, it is the one real ambiguity in the master prompt

Master prompt §3.2 assigns PDP masking to you, but also requires masking to happen **during load**,
and the loader is Backend's code (`analytics/cdc/**`). Two agents must never write one file. Resolved:

- **You define the policy.** `analytics/warehouse/` contains the DDL creating
  `warehouse.column_policy(source_table, source_column, pdp_class, transform, mask_null)`, populated
  from `custom_pdp_core`. This table is the machine-readable instruction set.
- **Backend's loader reads that table and applies it.** Backend writes no policy of its own and
  invents no transform.
- You **verify** the outcome with dbt tests asserting masked columns are unreadable in marts.

If you find yourself editing `analytics/cdc/**`, you have drifted. Raise it to the Lead.

## Scope — in

### A. Warehouse storage (`analytics/warehouse/`)
- `init/` DDL: schemas `raw`, `staging`, `marts`, `warehouse` (metadata).
- `raw.*` landing tables: an unmodified mirror of source columns **plus `_ingested_at`, `_op`
  (`I|U|D`), `_tenant_id`, `_lsn`**. Append-only. No transformation on the way in.
- `warehouse.column_policy` — the contract above.
- `warehouse.pipeline_state(tenant_id, source_table, last_lsn, last_success_at, rows_loaded, ...)` —
  this is what `meta.last_refreshed_at` and `meta.is_stale` are served from (metric contract §3).
  The dashboard's "last refreshed at" reads real pipeline metadata, never a clock.
- **RLS policies** on every fact and dimension, keyed on a session variable carrying `tenant_id`.
  Master prompt §3.3: enforce at the storage layer, not only in application queries.
- `warehouse.access_audit` — every warehouse access path must be attributable (§3.2), mirroring the
  approach in `custom_pdp_audit` if that module exists; if it does not, design it and say so.

### B. dbt project (`analytics/dbt/`)
- `dbt-postgres`, layered `stg_` → `int_` → `mart_`, with `sources:` declared against the `raw` schema.
- **Facts (minimum):** `fct_sale_order_line`, `fct_account_move_line`, `fct_stock_move`,
  `fct_pos_order_line`, `fct_ppob_transaction`.
- **Dimensions:** `dim_date` (**with Indonesian fiscal calendar and national holidays** — seed them),
  `dim_partner`, `dim_product`, `dim_company`, `dim_operating_unit`, `dim_tenant`.
- **`dim_partner` and `dim_product` are SCD Type 2** via dbt snapshots. **State your surrogate key
  strategy explicitly** in a README — the master prompt asks for it by name.
- Every mart respects tombstones: a row whose latest `_op` is `D` must not appear. This is the
  behaviour the delete test in §6 exercises.
- Marts must be able to answer every metric in contract 03, at the declared grain.

### C. Data quality (§3.4) — the part that must fail loudly
- dbt tests on **every** model: `not_null` + `unique` on keys, `relationships` on every FK,
  `accepted_values` on every state/status column (including `ppob.transaction.state`).
- **Reconciliation tests**, per day per tenant, asserting warehouse totals match Odoo source totals for:
  revenue, journal-entry balance (**debit == credit**), and stock quantity.
- A failing reconciliation is `severity: error` and **fails the pipeline**. It must not be a warning.
  A warehouse that quietly serves a wrong number is worse than one that is down.
- **Idempotency test**: run the load over the same source range twice and diff the marts — identical.
  Master prompt §3.0 requires this proven, not asserted.

### D. Integration (§3.6)
- `docker-compose.analytics.yml` following the conventions in contract 04 exactly: same anchors,
  `no-new-privileges`, `cap_drop: [ALL]`, `${COMPOSE_PROJECT_NAME:-odoo19-bct}-` naming.
  Warehouse Postgres on **`127.0.0.1:35433`** (reserved for you; do not take another port).
- Makefile targets `up-analytics`, `dbt-run`, `dbt-test`, `warehouse-backup` — **check contract 04
  for the namespace already taken and do not collide.** `warehouse-backup` reuses the
  `scripts/tenant-backup.sh` conventions.
- `observability/prometheus/analytics-alerts.yml` — alert rules for **replication slot lag**
  (warn 512 MiB, critical 1 GiB, per the accepted ADR) and **reconciliation failure**.
- `observability/grafana/analytics-pipeline.json` — a dashboard with a **replication slot lag panel**
  (§6 requires the alert be tested by stopping the pipeline), rows/sec, per-table end-to-end lag, last
  successful run, failure count.

## Scope — out
- `analytics/cdc/**`, `analytics/semantic-api/**`, `scripts/analytics/**`, `login-gateway/**` —
  **Backend agent.** You define the column policy; Backend applies it.
- `insight-portal/**` — Frontend.
- `.github/workflows/**` — **Security owns `ci.yml`. You do not edit it.** To get your image into the
  `container-scan` matrix or to add the `dbt-ci` job, send the Lead a diff request for Security to
  merge. This is the §2.1 conflict rule.
- `docker-compose.yml`, `Makefile`, `scripts/**` (except `scripts/analytics/`), `odoo/**`,
  `postgres/**` — Platform-Infra. **Exception:** you own `docker-compose.analytics.yml` and you may
  append your four targets to the `Makefile` — coordinate through contract 04 so you do not clobber
  Platform-Infra's targets.
- `addons/**` — Platform-Addons.

## Contracts consumed
- `04-platform.md` — network, volumes, roles, env names, ports, Makefile namespace.
- `01-classification.md` + `custom_pdp_core`'s seeded map — the classification you must materialise.
- `custom_pdp_masking`'s documented HMAC construction — your policy table must name exactly that
  transform so Backend's loader reproduces it byte-identically.

## Contracts produced — publish to `docs/agents/contracts/05-warehouse.md`
- `warehouse.column_policy` schema and semantics — **Backend's loader depends on this.**
- `warehouse.pipeline_state` schema — Backend's semantic-api serves `last_refreshed_at` from it.
- The `raw.*` table naming convention and required metadata columns, so Backend's loader writes rows
  your `stg_` models can read.
- The RLS session variable name and how a caller sets it.
- The mart list with grain, so Backend can bind `source_model` in the metric contract.

## Constraints
- **Never write to the Odoo database.** Connect only as `warehouse_reader`. Anti-pattern §7.10.
- No unmasked personal data may exist in `raw` at any point — not even transiently with a plan to
  mask later (anti-pattern §7.5). If your design requires that, escalate instead.
- Do not build marts on Odoo's `*_report` SQL views (anti-pattern §7.4).
- Do not add Airflow/Dagster/Kafka. The ADR settled the scheduler question.
- Warehouse Postgres budget ≤ 1 GiB under load; the VPS already carries ≈1.29 GiB measured.
- **Other live Docker stacks on this host must not be disturbed** (`odoo19-platform-*`,
  `odoo19-analytics-*`, `smart-warga-postgres-1`). Always scope compose commands `-p odoo19-bct`.
  Never `docker system prune` / `docker volume prune` / unscoped `down`.

## Acceptance criteria — testable statements only
1. `dbt build` completes green against a seeded database; paste the model/test counts.
2. `dbt test` includes reconciliation tests that **fail the run** (not warn) when totals disagree.
   Prove it: deliberately perturb one figure, show the run failing, restore it, show it passing.
3. Debit == credit reconciliation passes per day per tenant on real seeded data.
4. Running the load twice over the same range produces **identical** marts — paste the diff showing
   zero rows differing.
5. A tenant-scoped connection returns **zero rows** for another tenant's data, enforced by RLS.
   Paste the query and the empty result, plus proof RLS is on (`pg_policies`).
6. A field classified `personal` is unreadable in the mart — paste the actual stored value.
7. A `secret`-class column **does not exist** as a warehouse column at all.
8. `dim_partner` and `dim_product` produce a genuine SCD2 history: change a record, show two rows
   with correct validity ranges.
9. `dim_date` contains Indonesian national holidays — paste a sample.
10. Every fact and dimension carries `tenant_id`.
11. Grafana dashboard JSON loads, and the slot-lag alert rule is syntactically valid
    (`promtool check rules`).

## Evidence required — paste the output of exactly these
```
make up-analytics && docker compose -p odoo19-bct ps
make dbt-run 2>&1 | tail -30
make dbt-test 2>&1 | tail -40
docker compose -p odoo19-bct exec -T warehouse-db psql -U warehouse -d warehouse -c "\dt marts.*"
docker compose -p odoo19-bct exec -T warehouse-db psql -U warehouse -d warehouse -c "select schemaname,tablename,policyname from pg_policies order by 1,2;"
docker compose -p odoo19-bct exec -T warehouse-db psql -U warehouse -d warehouse -c "set app.tenant_id='tenant_a'; select count(*) from marts.fct_sale_order_line where tenant_id<>'tenant_a';"
docker compose -p odoo19-bct exec -T warehouse-db psql -U warehouse -d warehouse -c "select partner_key, name from marts.dim_partner limit 5;"
docker compose -p odoo19-bct exec -T warehouse-db psql -U warehouse -d warehouse -c "select date_day, sum(debit)-sum(credit) as imbalance from marts.fct_account_move_line group by 1 having sum(debit)<>sum(credit) limit 5;"
promtool check rules observability/prometheus/analytics-alerts.yml
python -c "import json;json.load(open('observability/grafana/analytics-pipeline.json'));print('GRAFANA_JSON_OK')"
```

## Escalation triggers — stop and return to Lead
- The classification map from `custom_pdp_core` is missing a column you must load. **Do not default it
  to `public`** — the loader is specified to hard-fail on unclassified columns, and so should you.
- Reconciliation cannot be made to pass and you suspect the source data, not your model. Report the
  discrepancy with numbers; do not tune the test until it goes green.
- RLS cannot be enforced for some model without application-level filtering.
- You need to edit `analytics/cdc/**` or `ci.yml`.
- Meeting a freshness SLA from the ADR appears infeasible on this hardware — report measured lag.
