# Analytics warehouse — design notes

Owner: **Data Warehouse agent**. Scope: `analytics/warehouse/**`, `analytics/dbt/**`,
`compose/insight.yml`, `observability/*analytics-*`.

Read `docs/adr/0001-analytics-warehouse.md` first — it is binding — then
`docs/agents/contracts/05-warehouse.md`, which is the seam with Backend.

---

## 1. Surrogate key strategy

*The master prompt asks for this by name. It is the decision the rest of the star hangs off.*

Every dimension carries **two** keys, doing two different jobs.

### The durable key — `<entity>_key`

```
partner_key = md5(tenant_id || '|' || partner_id)
product_key = md5(tenant_id || '|' || product_id)
```

Implemented once, in `macros/warehouse.sql::surrogate_key`, and used identically by facts and
dimensions. Three properties, each of which is the reason for a specific alternative being rejected:

**1. A fact computes its own foreign key. It never looks the dimension up.**
`fct_sale_order_line` holds `partner_id` already, so it computes `partner_key` from it directly. So
a fact never waits for a dimension to be built, there is no late-arriving-dimension deadlock, and
`dbt build` can order the two either way. A lookup-based key would make every fact depend on every
dimension it references and turn one slow dimension into a stalled build.

**2. It survives `--full-refresh`.** This is why it is a hash and not a sequence. A
`GENERATED ALWAYS AS IDENTITY` surrogate is assigned at insert time, so dropping and rebuilding the
dimension reassigns every key — and every fact that stored one is now silently pointing at a
different entity. Nothing errors. The reports are just wrong. A deterministic hash of the natural
key cannot do that: rebuild it a thousand times and it is the same value.

**3. `tenant_id` is the first component, always.** Two tenants both have a partner with id 42. They
are two entities with two independent histories, and the key says so. This is also what makes RLS
sound at the dimension level: a key cannot collide across a tenant boundary.

Two NULL hazards, both handled in the macro rather than at each call site:

- a NULL component becomes the literal sentinel `_dbt_null_` rather than collapsing the whole
  `concat` to NULL;
- components are joined with `|`, which cannot occur in an integer id or in a tenant slug (the slug
  regex is `^[a-z][a-z0-9_]{1,30}$`), so `('bct', 12)` and `('bct1', 2)` cannot collide.

### The version key — `<entity>_version_key`

```
partner_version_key = md5(partner_key || '|' || valid_from)
```

Unique per **row of history**. This is what `unique` is tested on. `partner_key` is deliberately
**not** unique in an SCD2 dimension — a dimension with history has many rows per entity, and a
`unique` test on the durable key would quietly force the history out of the model.

The test that actually protects SCD2 correctness is neither of those:

```yaml
- unique_combination:
    arguments: {combination_of_columns: [tenant_id, partner_key, is_current]}
```

**Exactly one current version per entity.** A broken close-out — two rows both `is_current` — does
not raise an error anywhere. It shows up as every report that joins the dimension silently
double-counting, which is the kind of defect that survives a release.

### Joining

```sql
-- current state
join marts.dim_partner d on d.partner_key = f.partner_key and d.is_current

-- as of the event
join marts.dim_partner d on d.partner_key = f.partner_key
                        and f.date_day >= d.valid_from::date
                        and f.date_day <  d.valid_to::date
```

`valid_to` is `9999-12-31` rather than NULL on the current row, so the half-open range predicate
needs no `coalesce` and no `or ... is null` branch that somebody will forget.

### The unknown member

`dim_partner` and `dim_product` each carry one synthetic row per tenant, keyed on
`md5(tenant_id || '|_dbt_null_')`, flagged `is_unknown`. `dim_operating_unit` carries the same idea
as `operating_unit_id = -1`, flagged `is_unassigned`.

They exist so that **every `relationships` test in the project is a plain test with no `where`
clause and no exception**. POS orders and PPOB transactions are routinely anonymous; 120 of the 431
seeded journal items have no product. Without an unknown member those foreign keys would be NULL,
the referential test would need excusing — and an excused foreign-key test is exactly how a
genuinely broken key stops being noticed.

---

## 2. Why the landing zone is generated, not hand-written

`raw.*` DDL is produced by `bin/warehouse_ctl.py gen-raw-ddl` from `warehouse.column_policy`, which
is itself synced from `custom_pdp_core`. `warehouse_loader` — Backend's identity — holds **no
`CREATE`** anywhere.

That is not bureaucracy. `CREATE TABLE` is the exact point where two contract-01 guarantees stop
being conventions and become structural facts:

- a **`secret`** column has no name in the landing table, so nothing downstream can select it. Not
  "is filtered out", not "is NULL" — *does not exist*. Asserted by
  `tests/assert_secret_columns_absent_from_warehouse.sql` against `information_schema`.
- an **unclassified** column cannot land, because the generator hard-fails before emitting DDL for
  a column with no policy row.

A loader able to create its own landing table could land an unclassified column, and the whole
guarantee would degrade to "the loader promises not to".

### The guard that "hard-fail on unclassified" would have missed

`res.partner.barcode` **was** classified — as `personal`, meaning `hmac_sha256`. Its physical type
in Odoo 19 is `jsonb`, and the pinned HMAC construction takes `str` or `None` and nothing else. The
generator therefore also refuses a digest transform pointed at a non-text column, and says so as a
contract question rather than casting around it. That is what surfaced the issue; contract 01 was
amended and the column is now `sensitive` + `drop_to_null`.

---

## 3. The dev fixture loader is not the pipeline

`bin/warehouse_ctl.py load-fixture` performs a **policy-driven masked snapshot load** so the marts
can be built and tested independently of Backend's CDC consumer. It is a fixture:

- no WAL, no replication slot, no resumability — `_lsn` is NULL on every row it writes;
- it appends `_op='I'` only, and it never updates or deletes;
- streamed CDC rows carry a real `_lsn`, and `coalesce(_lsn, '0/0')` sorts NULL first, so **every
  real CDC row supersedes every fixture row for the same key**. That precedence is what makes a
  re-snapshot safe to run over live data.

It applies the same policy through the same HMAC — `warehouse.pdp_hmac()`, whose four published
known-answer vectors and three negative vectors are asserted on every `warehouse-apply.sh` run — so
its digests are byte-identical to the ones Backend's loader produces.

Backend's `analytics/cdc/**` is the pipeline. This is a test fixture and a re-seed path.

---

## 4. Two things that look like nits and are not

**`FORCE ROW LEVEL SECURITY`, not just `ENABLE`.** The marts are owned by `warehouse`, and a table
owner is exempt from its own policies unless the table is forced. Enabled-but-not-forced RLS on an
owner-queried table protects nothing while looking like it does.

**RLS is applied by a dbt `post-hook`, on every model, every run.** `dbt build --full-refresh`
drops and recreates a table, and a policy created by hand goes with it. Nothing errors; every query
still returns rows; the tenant boundary is simply gone. A control that disappears on a full refresh
is a control nobody re-checks — so `warehouse.apply_tenant_rls()` runs as part of the model
definition, and `tests/assert_rls_forced_on_every_mart.sql` asserts the result independently.

---

## 5. Files

| Path | What |
|---|---|
| `init/00-bootstrap.sh` | applied by the Postgres entrypoint on **first boot** |
| `init/sql/*.sql` | the DDL — idempotent, applied by both paths so they cannot drift |
| `bin/warehouse-apply.sh` | re-applies `init/sql/*.sql` to a **running** warehouse |
| `bin/warehouse_ctl.py` | `sync-policy`, `gen-raw-ddl`, `gen-fdw`, `load-fixture`, `tombstone`, `verify` |
| `bin/warehouse-backup.sh` | `pg_dump` + manifest + SHA256SUMS, and the restore path |
| `exporter/queries.yml` | the `bct_warehouse_*` Prometheus series |
| `postgresql.conf` | analytics-shaped: `wal_level=replica`, few connections, large `work_mem` |
