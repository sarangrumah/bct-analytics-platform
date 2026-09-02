# Runbook — analytics pipeline

Operating the Odoo → warehouse CDC pipeline: what breaks, how to tell which thing broke, and the
exact commands that fix it.

Every claim here was executed against the running `odoo19-bct` stack. Where a command's behaviour
differs from what a flag's help text promises, this runbook says so rather than repeating the help
text.

**Scope every compose command to this project.** This host also runs `odoo19-platform-*`,
`odoo19-analytics-*` and `smart-warga-postgres-1`. An unscoped `docker compose down`, a
`docker system prune` or a `docker volume prune` destroys their data, and there is no undo.
Everything in this runbook either goes through the `Makefile` (which carries `-p odoo19-bct` on
every compose line) or names containers explicitly.

---

## 0. The thirty-second orientation

| Piece | What it is | Where it runs |
|---|---|---|
| `odoo19-bct-postgres` | Odoo's OLTP database, `wal_level=logical`, `max_slot_wal_keep_size=2GB` | container |
| publication `bct_cdc_<slug>` | the set of tables Postgres decodes | inside the OLTP database |
| slot `bct_slot_<slug>` | Postgres's bookmark; **retains WAL while it exists** | inside the OLTP database |
| `odoo19-bct-cdc` | the loader: backfill, then `pgoutput` stream, masking applied in flight | container |
| `odoo19-bct-warehouse-db` | the warehouse: `raw` → `staging` → `marts` | container |
| `raw.*` | append-only landing zone; a delete is a tombstone row, never a removal | warehouse |
| `warehouse.column_policy` | which column is which PDP class, and therefore what is masked | warehouse |
| `warehouse.pipeline_state` | the **only** source of `meta.last_refreshed_at` / `meta.is_stale` | warehouse |
| dbt | builds `staging` and `marts` from `raw` | `make dbt-run` |

Two facts do more work than any others when diagnosing:

1. **The loader creates nothing.** `warehouse_loader` holds no `CREATE`, no `UPDATE` and no
   `DELETE`. A missing `raw.*` table is a schema-drift signal, not something to fix in flight.
2. **A role with no privilege on a schema sees its tables as *absent*, not as inaccessible.** An
   empty `\dt warehouse.*` is ambiguous between "the DDL never ran" and "this role cannot see it".
   Check `\du` and the grants before concluding the DDL is missing.

---

## 1. First response: which layer is broken?

Run these four in order. The first one that looks wrong is the layer to work on.

```bash
# 1. is the loader alive at all?
docker ps --filter name=odoo19-bct-cdc --format '{{.Names}}\t{{.Status}}'
docker logs --tail 40 odoo19-bct-cdc

# 2. does the slot exist, is it consumed, and how much WAL is it holding?
docker exec odoo19-bct-postgres psql -U odoo -d bct -c "
  SELECT slot_name, active, wal_status,
         pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained
  FROM pg_replication_slots ORDER BY slot_name;"

# 3. is the landing zone still moving?
docker exec odoo19-bct-warehouse-db psql -U warehouse_admin -d warehouse -c "
  SELECT tenant_id, source_table, last_lsn,
         round(extract(epoch FROM now() - last_success_at)) AS age_seconds,
         failure_count, left(coalesce(last_error,''), 60) AS last_error
  FROM warehouse.pipeline_state ORDER BY age_seconds DESC LIMIT 10;"

# 4. what does the serving layer believe about freshness?
docker exec odoo19-bct-warehouse-db psql -U warehouse_admin -d warehouse -c "
  SELECT mart_name, tenant_id, sla_seconds, age_seconds, is_stale
  FROM warehouse.mart_freshness WHERE is_stale ORDER BY age_seconds DESC;"
```

Reading the answers:

| Symptom | Layer | Section |
|---|---|---|
| loader not running, or exiting on start | loader | §2 |
| `wal_status = 'lost'`, or the slot is gone | **slot dropped by the cap** | §3 |
| slot `active = f` and `retained` climbing | consumer stopped | §4 |
| slot healthy, `pipeline_state` ageing | loader stuck or idle | §5 |
| `pipeline_state` fresh, marts stale | dbt not running | §6 |
| everything fresh, numbers wrong | reconciliation | §7 |

---

## 2. The loader will not start

`docker logs odoo19-bct-cdc` names the cause; these are the ones with a non-obvious fix.

**`publication bct_cdc_<slug> does not exist`**
```bash
bash scripts/analytics/cdc-provision.sh
```
It runs as the `odoo` role on purpose: `CREATE PUBLICATION` needs table ownership and
`warehouse_reader` correctly does not have it.

**`Cannot use the contract-05 warehouse tables … (absent, or invisible to this role)`**
The warehouse DDL has not been applied, *or* the loader's role lost its grants. Check which before
re-running anything:
```bash
docker exec odoo19-bct-warehouse-db psql -U warehouse_admin -d warehouse -c "\du" \
  -c "SELECT count(*) FROM warehouse.column_policy;"
make up-analytics     # applies the DDL, syncs the policy, generates the raw DDL. Idempotent.
```

**`No masking salt for tenant …`** — set `WAREHOUSE_MASK_SALT_<TENANT>` (or
`WAREHOUSE_MASK_SALT_DEFAULT`). The loader refuses to start rather than hash without a key, and it
is right to: an unkeyed digest of an email address is reversible by dictionary attack.
**Never change a salt on a populated warehouse.** Every existing digest was computed with the old
one, so the same person gets two different join keys and their history silently splits. Changing a
salt is a full re-load, not a configuration change.

**`digest spec mismatch`** — `bct_cdc.pdp_hash` and `custom_pdp_masking` disagree. Do not "fix" one
side to match the other without deciding which is right: if the module changed, every digest already
in the warehouse is invalidated and the change is a migration.

---

## 3. The replication slot was dropped or invalidated by the 2 GB cap

**This is the scenario ADR 0001 designed for, and it is a deliberate outcome, not a malfunction.**
`max_slot_wal_keep_size = 2GB` means Postgres will discard WAL a lagging consumer has not read
rather than let the disk fill and take Odoo down. The warehouse is expendable; the ERP is not.

### 3.1 Confirm it, and confirm Odoo is fine

```bash
docker exec odoo19-bct-postgres psql -U odoo -d bct -c "
  SELECT slot_name, active, wal_status, safe_wal_size,
         pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained
  FROM pg_replication_slots;"
```

- `wal_status = 'lost'` — invalidated. The WAL this slot needed is gone.
- **no row at all** — the slot was dropped outright.
- Either way check Odoo first: `curl -sf -o /dev/null -w '%{http_code}\n' http://127.0.0.1:38069/web/login`
  should be `200`, and `df -h` on the Docker volume should have recovered. If Odoo is healthy, the
  cap did exactly its job.

The loader treats this as **fatal and exits non-zero** rather than reconnecting. That is
deliberate: reconnecting to an invalidated slot resumes from a later position and leaves a hole in
the mart with no error anywhere — the failure mode that looks like success.

### 3.2 Re-seed. There is no partial recovery from an invalidated slot

The lost WAL is lost. Every change between the slot's last confirmed LSN and now is unrecoverable
from the WAL, so the only correct repair is a fresh snapshot. Do **not** simply restart the loader
and hope.

```bash
# 1. Stop the loader, if anything is still running.
docker rm -f odoo19-bct-cdc

# 2. Remove the dead slot. (Skip if it was dropped outright.)
docker exec odoo19-bct-postgres psql -U odoo -d bct \
  -c "SELECT pg_drop_replication_slot('bct_slot_bct');"

# 3. Re-create publication and slot. Creating the slot is what starts WAL retention again,
#    so from this instant nothing new is lost.
bash scripts/analytics/cdc-provision.sh

# 4. Clear the landing zone for the affected tenant so the snapshot is not merged into a
#    partial history. Requires warehouse_admin: the loader holds no DELETE, by design.
docker exec odoo19-bct-warehouse-db psql -U warehouse_admin -d warehouse -c "
  DO \$\$ DECLARE t text; BEGIN
    FOR t IN SELECT DISTINCT source_table FROM warehouse.column_policy LOOP
      EXECUTE format('DELETE FROM raw.%I WHERE _tenant_id = %L', t, 'bct');
    END LOOP;
  END \$\$;
  DELETE FROM warehouse.pipeline_state WHERE tenant_id = 'bct';"

# 5. Snapshot, then stream.
bash scripts/analytics/cdc-run.sh --detach

# 6. Rebuild the marts from the new landing zone.
make dbt-run
```

**Why step 4 is not optional.** The backfill resumes from `max(id)` already landed. If the old rows
are left in place, the snapshot skips everything below that id — including every row that changed
during the outage — and the warehouse is left quietly wrong. Step 4 is the difference between a
re-seed and a no-op.

### 3.3 Verify the re-seed rather than assuming it

```bash
bash tests/run.sh -k "reconciliation"
bash tests/run.sh -k live_sync
```

The first proves the warehouse's totals equal Odoo's, per table and per day. The second proves a
create, an update and a delete travel end to end. Both print the numbers they compared.

### 3.4 Preventing the next one

An invalidation is always preceded by ten or more minutes of alerting; if it happened silently, the
alerting is the defect, not the slot.

```bash
make up-obs   # Prometheus, Alertmanager and the exporters
```
- `ReplicationSlotWalRetentionWarning` at 512 MiB retained, `for: 10m`
- `ReplicationSlotWalRetentionCritical` at 1 GiB, `for: 5m`
- `ReplicationSlotInvalidated` on `wal_status="lost"`, `for: 1m`
- `ReplicationSlotInactive` on no consumer, `for: 15m`

Their behaviour at exactly those thresholds — including that they do **not** fire just below them —
is proved by `tests/prometheus/slot_lag_alerts_test.yml`, run by
`bash tests/run.sh -k slot_alerts_fire`.

> **NOT PROVEN: that alerting is live after a cold start.** Stated here rather than left to be
> assumed, because this section is where an operator comes to satisfy themselves that the safety net
> is up.
>
> `make up-dev` and `make up-analytics` do not touch the observability overlay, so a teardown leaves
> Prometheus, Alertmanager, Loki, promtail and node-exporter down -- and every rule above then fires
> into nothing while `make verify` still passes. The cold-start suite brings the overlay back and
> asserts `make check-alerting`, but **that assertion has never yet run inside a cold start**, so
> the end-to-end claim is unverified. The command that will prove it:
>
> ```bash
> BCT_COLDSTART=i-understand-this-destroys-the-bct-oltp-data make test-coldstart
> ```

### `make check-alerting` -- what it now proves, and how it failed before

Run it after `make up-obs`. It is the fastest way to find out whether the alerting above is real:

```bash
make check-alerting
#   scrape targets: 5/5 up
#   alertmanagers: 1 configured, http://127.0.0.1:39093/-/ready answering
#   alerting rules: 24 evaluated, 21/21 referenced metrics have current samples
# check-alerting: OK
```

Exit codes: **0** pass, **non-zero** fail, **77** skip (Prometheus not running). `ALLOW_SKIP=1`
downgrades a skip to 0 for an environment where the overlay is deliberately absent.

Two earlier versions of this check passed while proving nothing, and both are worth knowing because
an operator who remembers "check-alerting was green" may be remembering one of them:

1. It probed `/-/healthy`, which returns **plain text**, and JSON-decoded it. The resulting
   `JSONDecodeError` was caught as "Prometheus not reachable" and it returned **0** -- printing
   "NOT a pass" while exiting successfully. Every check below that line was unreachable code.
2. It then reported `alertmanagers: 1 active` **with Alertmanager stopped**, because
   `prometheus.yml` discovers it via `static_configs` and `/api/v1/alertmanagers` reports the
   *configured* target whether or not anything is listening. Alertmanager is also not a scrape
   target, so the targets check did not cover it either. A firing alert would have gone nowhere
   with every gate green.

It now probes Alertmanager directly at `ALERTMANAGER_URL`. Verified in both directions:

```
alertmanager stopped -> check-alerting: FAIL ... "1 configured, NONE answering"   (non-zero)
alertmanager started -> check-alerting: OK                                        (rc 0)
```

If Alertmanager is deliberately not published on `127.0.0.1:39093`, set `ALERTMANAGER_URL` rather
than ignoring the failure.

---

## 4. The consumer stopped and WAL is piling up

You have time: at 512 MiB the warning fires with 75% of the cap still in hand.

```bash
docker logs --tail 100 odoo19-bct-cdc      # if the container is gone, `docker logs` will say so
bash scripts/analytics/cdc-run.sh --detach
```

Then watch retention fall:
```bash
watch -n 5 "docker exec odoo19-bct-postgres psql -U odoo -d bct -tAc \
  \"SELECT slot_name, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) \
    FROM pg_replication_slots;\""
```

If retention does **not** fall within a few minutes of the consumer restarting, the consumer is
running but not confirming — go to §5.

**If the slot belongs to a tenant that no longer exists, drop it.** An orphan slot retains WAL
forever and buys nothing:
```bash
docker exec odoo19-bct-postgres psql -U odoo -d bct \
  -c "SELECT pg_drop_replication_slot('bct_slot_<dead_tenant>');"
```

---

## 5. The slot is healthy but `pipeline_state` is ageing

`last_success_at` is advanced by a heartbeat on a 15 s timer in its own thread, so it moves on an
**idle** pipeline as well as a busy one. Age above ~20 s therefore means the loader is genuinely not
working, not merely that nothing happened.

Distinguish three cases:

```bash
# Is it erroring in a loop?
docker exec odoo19-bct-warehouse-db psql -U warehouse_admin -d warehouse -c "
  SELECT tenant_id, source_table, failure_count, last_error
  FROM warehouse.pipeline_state WHERE failure_count > 0 ORDER BY failure_count DESC;"

# Is it blocked on the warehouse?
docker exec odoo19-bct-warehouse-db psql -U warehouse_admin -d warehouse -c "
  SELECT pid, state, wait_event_type, wait_event, left(query, 80)
  FROM pg_stat_activity WHERE usename = 'warehouse_loader';"

# Is the source producing anything at all?
docker exec odoo19-bct-postgres psql -U odoo -d bct -c "SELECT pg_current_wal_lsn();"
```

`SchemaDrift` in the log means a table or column the policy declares does not exist in `raw`.
**Do not create it by hand.** DWH generates that DDL from the policy:
```bash
make up-analytics       # includes the raw-DDL generation step; idempotent
```

---

## 6. `pipeline_state` is fresh but the marts are stale

The landing zone is current and dbt has not run. `warehouse.mart_freshness` is computed from
`pipeline_state`, so this shows up as marts whose row counts stop changing rather than as a stale
flag.

```bash
make dbt-run     # dbt build, excluding tests
make dbt-test    # the dbt tests, including reconciliation against live Odoo
```

If `dbt build` fails on a model, read the first error only — dbt reports downstream failures for
every model that depended on the one that actually broke.

**`--full-refresh` drops and recreates a table.** Contract 05 handles the consequence: RLS is
applied by a `post-hook` on every model, so it survives the recreation. If you ever apply a policy
to a mart by hand, it will not.

---

## 7. Reconciliation is failing

Reconciliation compares the warehouse's totals to Odoo's. A failure means the warehouse is *wrong*,
not merely late, and the numbers tell you which kind of wrong.

```bash
bash tests/run.sh -k reconciliation -s
```

The output prints, side by side, per table and per day:

| Failure shape | What it means | Where to look |
|---|---|---|
| one table's row count is low | rows never landed | that table's `pipeline_state` row; `raw` for tombstones with no matching insert |
| one table's row count is high | duplicate keys in the live projection | ordering key `( _tenant_id, id, _lsn)`; two rows sharing an LSN |
| counts match, amounts differ | a column replicates but carries a wrong value | the column's `warehouse.column_policy` row — a column that became `personal` is now a digest |
| `debit != credit` | a journal line was lost or duplicated | `raw.account_move_line`; this identity holds in Odoo by construction, so the fault is ours |
| one day is wrong, others fine | a gap in the stream | slot lag around that timestamp; §3 |

**First question, always: was the slot invalidated at any point?** An invalidation leaves exactly
this signature — most numbers right, some silently missing — and §3 is the fix. Reconciling a
warehouse with a hole in it by adjusting the query is how a hole becomes permanent.

The suite also asserts that the dataset spans at least two Operating Units, so that an OU-scoping
bug cannot hide behind a single-OU dataset. If that assertion fails, the *data* is inadequate for
the test, not the pipeline.

---

## 8. Re-loading a range

There is deliberately **no `--reload` flag.** It existed briefly and crashed on every invocation
(`AttributeError: module 'bct_cdc.backfill' has no attribute 'clear_completion'`), because it
belonged to an earlier design that tracked completion in a side table. When the resume point moved
into the landing zone itself there was nothing left for it to clear, so it was removed rather than
reimplemented. `analytics/cdc/tests/test_cli_flags.py` now asserts that every advertised flag is
actually dispatched, so a flag that does not work fails a test instead of an incident.

**The safe path is simply to re-run the backfill.** It resumes from the highest id already landed,
so a repeat over a complete range reads nothing and appends nothing. Verified on this stack: a
second `--backfill-only` over the same range left the live projection of eight tables unchanged and
appended **zero** rows.
```bash
bash scripts/analytics/cdc-run.sh --name odoo19-bct-cdc-reload -- --backfill-only
```

**When you actually need rows re-read**, delete the range first as `warehouse_admin`, then backfill.
The resume point is derived from the data, so removing rows *is* how you ask for them again:
```bash
docker exec odoo19-bct-warehouse-db psql -U warehouse_admin -d warehouse   -c "DELETE FROM raw.ppob_transaction WHERE _tenant_id = 'bct' AND id > 5000;"
bash scripts/analytics/cdc-run.sh --name odoo19-bct-cdc-reload -- --backfill-only
make dbt-run
```

**A high ratio of landed rows to live rows is normal; a duplicate *change* is not.** On this stack
`raw.ppob_transaction` holds 28,110 rows for 9,610 live keys — roughly 3x — and none of that is
error. It is (a) ordinary append-only versioning, since Odoo's ORM emits an `INSERT` followed by
computed-field `UPDATE`s per record, and (b) the deliberate backfill/stream overlap: a row that
changes while the slot exists lands once from the snapshot and once from the stream at a higher LSN.
That is documented at-least-once behaviour and the latest-version-per-key projection resolves it.

The number that would indicate a real problem is different:

| Metric | Healthy | What a non-zero value means |
|---|---|---|
| `bct_cdc_landing_row_amplification` | a few x | landed rows per live key. Climbing without bound means re-reads. |
| `bct_cdc_landing_duplicate_changes` | **0** | two rows sharing `(id, _op, _lsn)` — the same change landed twice. Counted only among rows that *have* an LSN, because two NULL-`_lsn` rows compare equal and would read as a duplicate when they are two different changes. |
| `bct_cdc_landing_unordered_rows` | **0** | rows with a NULL `_lsn`. They **do** still reach the marts — dbt's `raw_latest` orders by `coalesce(_lsn, '0/0')`, so a NULL sorts last in precedence and any real CDC row supersedes it for the same key, which is what makes a re-snapshot safe over live data. What a NULL costs is narrower and still worth fixing: while one exists, `(_tenant_id, pk, _lsn)` is not a *total* order, so two distinct changes can share a key. The CDC loader never writes one; historically they came from the fixture-loading path. |

> Contract 05 names `(_tenant_id, pk, _lsn)` as the ordering key and says nothing about what a NULL
> `_lsn` means. That silence was filled by assumption in two places before anyone checked the dbt
> macro — including in a Prometheus HELP string that would have rendered on a Grafana panel as an
> authoritative statement about someone else's model. The precedence rule is being written into the
> contract; until it is, read the macro rather than infer.

## 9. Interrupted backfill

Nothing to do: restart it. The resume point is `max(id)` already landed, read back out of
`raw.<table>` rather than from a progress table, so it cannot get ahead of the rows it describes.

Measured on this stack — `SIGKILL` mid-run, then restart:

```
live rows at kill        5545  (started from 3845, target 9610)
run 2 resumed from       id > 6706
run 2 landed             4065 rows   (expected exactly 4065)
final                    9610 rows, live-projection checksum identical to before
run 3 (nothing to do)    0 rows landed
```

---

## 10. Onboarding and offboarding a tenant

**Onboard.** One publication and one replication slot per tenant database.
```bash
make tenant-provision TENANT=acme
bash scripts/analytics/cdc-provision.sh          # publication + slot for the new tenant
CDC_TENANT_DB=acme CDC_TENANT_SLUG=acme bash scripts/analytics/cdc-run.sh --detach --name odoo19-bct-cdc-acme
```
The tenant needs a row in `warehouse.tenant_registry` and a salt in the environment named by that
row's `mask_salt_env` — which holds the **name** of the variable, never the value.
Slot names forbid dashes; `tenant_registry` enforces `^[a-z][a-z0-9_]{1,30}$` for that reason.

**Offboard.** Drop the slot *first*. A slot left behind after its database is gone retains WAL
forever:
```bash
docker rm -f odoo19-bct-cdc-acme
docker exec odoo19-bct-postgres psql -U odoo -d bct -c "SELECT pg_drop_replication_slot('bct_slot_acme');"
docker exec odoo19-bct-warehouse-db psql -U warehouse_admin -d warehouse \
  -c "UPDATE warehouse.tenant_registry SET active = false WHERE tenant_id = 'acme';"
```
Erasing that tenant's data from the warehouse is a separate, manual procedure — see
`docs/pdp-compliance.md` §5.

---

## 11. Backups

```bash
make warehouse-backup                                    # pg_dump + manifest + SHA256SUMS
make warehouse-restore FROM=backups/warehouse/<stamp>
make tenant-backup TENANT=bct                            # Odoo database AND filestore
```

The warehouse is **rebuildable** from Odoo plus the CDC pipeline, so a warehouse backup is a
convenience. The Odoo backup is not a convenience. If you have time for one, take the Odoo one.

Warehouse backups contain PDP digests and therefore fall under the retention rules in
`docs/pdp-compliance.md` §5.3 — an erasure that does not reach the backups is not complete.

---

## 12. Health check, in one paste

```bash
docker compose -p odoo19-bct ps
docker exec odoo19-bct-postgres psql -U odoo -d bct -c \
  "SELECT slot_name, active, wal_status, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) FROM pg_replication_slots;"
docker exec odoo19-bct-warehouse-db psql -U warehouse_admin -d warehouse -c \
  "SELECT count(*) FILTER (WHERE is_stale) AS stale, count(*) AS marts FROM warehouse.mart_freshness;"
bash tests/run.sh -m "not slow and not coldstart" -q
```

Healthy looks like: every container `Up`/`healthy`; one active slot per tenant with `wal_status`
`reserved` and retention in kilobytes; zero stale marts for an active tenant; the suite green with
its skips explained.
