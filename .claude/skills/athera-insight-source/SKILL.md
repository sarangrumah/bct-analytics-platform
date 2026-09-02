---
name: athera-insight-source
description: Connect a client's database to ATHERA Insight so it produces dashboards — the Odoo path or the bring-your-own-Postgres path. Use when asked to add a data source, build marts or metrics for a client, or onboard an Insight-only client that has its own application.
---

# Adding an Insight data source

Two shapes. The machinery is the same; what differs is where column classification comes from and how bespoke the marts are.

## Hard prerequisite, state it before promising anything

The client's database must be **PostgreSQL with `wal_level = logical`**, plus a role holding `SELECT` and `REPLICATION`. If it is MySQL, SQL Server, an API or a spreadsheet, none of this path applies and a different loader would have to be written. Establish this first — it decides whether the engagement is configuration or a project.

```sql
SHOW wal_level;                    -- must be 'logical'
SHOW max_slot_wal_keep_size;       -- must not be -1: an unbounded slot can fill the client's disk
```

## What is generic, and what is not

**Reusable as-is:** pgoutput streaming, keyset backfill, the landing zone, the masking engine, the four warehouse roles, FORCE RLS, tenant isolation, `semantic-api`, `insight-portal`. The Odoo digest check is already conditional (`runner.py`: `if settings.verify_digest_spec and settings.odoo_login`), so a non-Odoo source simply leaves `CDC_ODOO_LOGIN` unset and skips it — that check exists to agree with `custom_pdp_masking`, and a client's own application has no such module to agree with.

**Odoo-specific, and therefore the work:**

1. **Column classification — `warehouse_ctl.py import-policy`.** `sync-policy` reads `pdp_field_classification` out of Odoo; a non-Odoo client has no such table, and the loader **hard-exits with code 3** on any unclassified column. That exit is a feature: unclassified data must not land. So the classification comes from a CSV instead, into the same `warehouse.column_policy` — an additional source, not a second policy system.

```bash
# CSV columns: source_table,source_column,pdp_class[,nullable]
make import-policy FILE=policies/<client>.csv
```

`analytics/warehouse/policies/example-external-client.csv` is a worked example. Three things it does for you:

- **The transform follows the class.** `secret → drop`, `personal → hmac_sha256`, `sensitive → hmac_sha256` (or `hmac_sha256_nullable` with `nullable=true`). The database enforces the pairing with `column_policy_class_transform_ck`, so deriving it removes a whole class of mistake: a column classified correctly and transformed wrongly.
- **Every fault at once**, with line numbers. An unknown class, a missing column name, or `nullable` on a class that may not be nullable each fail the whole import — never a silent default to `public`.
- **Stale rows are pruned only for the tables the file names.** A global sweep would delete the Odoo tenants' policy the first time you import for an external client; the two share this table and each source is authoritative only for its own tables. Verified: importing 10 external rows left all 1067 Odoo rows intact.

2. **dbt models.** Staging is named for Odoo tables (`stg_res_partner`, `stg_account_move_line`). A client's schema is their own, so their marts are bespoke.
3. **The metric registry.** `analytics/semantic-api/metrics/core.yml` stands on those marts. New source, new metrics.

Points 2 and 3 are consulting-shaped work per client. That is the product, not a defect in it.

## Odoo client

```bash
make cdc-start TENANT=acme
make dbt-run
make dbt-test          # run it — four warehouse alert rules stay dark until it does
```

## Bring-your-own-Postgres client

1. Set `insight_source_kind='external_postgres'` on the registry row.
2. Get a `SELECT` + `REPLICATION` role on the client's database, and network reachability from the `cdc` container. **Across hosts this needs TLS and a firewall rule** — it is a replication link, not an HTTP call.
3. Load the classification for every replicated column. Unclassified is a refusal, by design.
4. Add a `cdc` service instance with `CDC_SOURCE_HOST`, `CDC_TENANT_DB`, `CDC_TENANT_SLUG`, the mask salt, and **no** `CDC_ODOO_LOGIN`.
5. Write staging + mart models for their schema, keeping `tenant_id` on every mart and the `apply_rls()` post-hook — that hook is what survives `--full-refresh`.
6. Add metrics to the registry.

## Rules that keep isolation real

- Every mart carries `tenant_id` and `ENABLE` + **`FORCE`** row level security. `FORCE` is what makes the policy apply to the table's owner too.
- Nothing queries as a superuser or a `BYPASSRLS` role. `semantic-api` connects as `warehouse_rls`, which has no unscoped policy at all — with `app.tenant_id` unset it reads zero rows, fail-closed.
- `insight-portal`'s `lib/semantic.ts` takes **no tenant argument**. There is no parameter through which a URL, header or cookie could change which tenant is queried. Do not add one.

## Verify

```bash
make dbt-run && make dbt-test
curl -s -X POST http://127.0.0.1:38200/v1/query -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' \
  -d '{"metric":"revenue_net","grain":"month","filters":{"tenant_id":"acme","date_range":["2025-01-01","2026-12-31"]},"limit":5}'
```

`date_range` is `[from, to]` — an array, not an object.

Then the two checks that actually prove something:

- **Cross-tenant must be 403.** Ask for another tenant's `tenant_id` with this token; the body must be byte-identical whether or not that tenant exists.
- **The zero must be evidence.** Assert the other tenant *has* rows before asserting this session sees none of them. A zero from an empty warehouse proves nothing. `tests/test_05_tenant_isolation.py` is the model.

## Reconciliation

`make dbt-test` includes the reconciliation models that compare warehouse totals against the source per table per day, plus the debit==credit identity. Freshness comes from `warehouse.pipeline_state.last_success_at`, which is written by a timer thread — so it ages on a stalled pipeline rather than on an idle one.
