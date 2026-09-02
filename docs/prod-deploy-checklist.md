# Production deploy checklist

Target: a single Biznet Gio VPS (Ubuntu 24.04) already carrying the application stack plus
Prometheus/Grafana/Loki. This is the **manual** procedure; there is no `cd.yml` yet, and
`docs/cicd-activation.md` §5 records that gap under its owner's name.

**Ownership note.** Security drafts the content of the sections marked **[SECURITY OWES]** and sends
them here through the Lead; QA owns the file. Those markers are left visible so a reader can tell
"not applicable" from "not yet written".

Every checkbox below is either a command whose output you can read, or a decision with a named
owner. A checkbox that is neither is a wish, and this document tries not to contain any.

---

## 0. Before you start: is this deploy safe to do at all?

- [ ] The `ci-gate` check is green on the commit being deployed.
- [ ] `bash tests/run.sh -ra` is green **on a fresh clone of that commit**, not on a working tree.
      This is a standing rule, not an abundance of caution: an unanchored `.gitignore` pattern once
      hid three install-critical files while every working-tree test passed, and a clean clone is
      the only thing that sees it. `tests/test_12_clone_install.py` does exactly this.
- [ ] A current backup exists **and has been restored somewhere at least once**. An untested backup
      is a belief, not a backup.
      ```bash
      make tenant-backup TENANT=bct
      make warehouse-backup
      ```
- [ ] You know the rollback path for *this* deploy and it is written down before you begin (§6).

---

## 1. Sizing — the numbers this design was accepted against

ADR 0001 sized against a measured comparable stack rather than an estimate:

| Component | Measured RSS |
|---|---|
| odoo | 452 MiB |
| postgres (OLTP) | 538 MiB |
| redis | 9 MiB |
| prometheus / grafana / loki / promtail / alertmanager | 293 MiB combined |
| **subtotal** | **≈ 1.29 GiB** |
| warehouse Postgres, idle | 66 MiB |

- [ ] The VPS has at least **8 GiB RAM** and enough disk for the Odoo filestore, the warehouse, the
      Prometheus TSDB **and 2 GiB of WAL headroom** for the replication slot.
- [ ] Free disk is checked *after* accounting for that 2 GiB. The slot cap protects Odoo from a slow
      consumer; it does not protect it from a disk that was already nearly full.

---

## 2. Secrets and configuration

- [ ] `.env` is generated on the host, never copied from a laptop:
      ```bash
      make dev-bootstrap          # generates .env with random values
      python3 scripts/gen-env-secrets.py --in-place
      ```
- [ ] `make scan-secret` passes: no real value has drifted into `.env.example`.
- [ ] Every `WAREHOUSE_MASK_SALT_*` is set and is **not** `changeme`. The loader refuses to start
      without one, which is correct — an unkeyed digest of an email address is reversible by
      dictionary attack.
- [ ] **The masking salts are recorded in the secret store before first load.** Losing a salt does
      not merely lose the ability to re-derive digests: changing one is a full warehouse re-load,
      because every existing digest was computed with the old key and the same person would acquire
      two identities.
- [ ] `LOGIN_GATEWAY_COOKIE_SECURE=1`. It is `0` in dev.
- [ ] `BIND_ADDRESS` is `127.0.0.1` and a reverse proxy terminates TLS. Nothing in this stack should
      be directly reachable from the internet.
- [ ] `ODOO_LIST_DB=False` and the Odoo master password is not the default.
- [ ] SOPS/age keys are on the host and the decrypt is verified: `security/SOPS-ONBOARDING.md`.

---

## 3. Bring-up order, and why it is this order

```bash
make up-dev                      # Odoo + Postgres + Redis; DB init; applies the dev password [5/5]
make seed-demo                   # demo volume + passwords its users. NOT run by up-dev, by design
make up-obs                      # Prometheus, Alertmanager, Grafana, exporters
make up-analytics                # warehouse-db, DDL, policy sync, raw DDL
bash scripts/analytics/cdc-provision.sh      # publication + slot, as the `odoo` role
bash scripts/analytics/cdc-run.sh --detach   # backfill, then stream
make dbt-run                     # build staging and marts
```

- [ ] **`make seed-demo` runs BEFORE `make up-analytics`, and the order is load-bearing.**
      `up-analytics` copies whatever Odoo holds *at that moment* into the `bct_t2` fixture tenant
      over FDW. Seeding afterwards leaves `bct_t2` with a registry row and almost no data — and
      nothing errors. Measured on a cold start that got this wrong: 10 rows landed instead of 2,109,
      every cross-tenant isolation assertion then passed by having nothing to leak, and `dbt test`
      returned **714** reconciliation failures, all of them `bct_t2`, with `bct` clean at 0/818.
      Re-running `make up-analytics` after the seed repairs it: 2,109 rows, and `dbt test` goes
      PASS=292 / ERROR=0.
- [ ] **Observability comes up before CDC.** Creating the replication slot starts WAL retention. If
      the consumer then fails and nobody is watching, the first thing anyone learns about it is a
      full disk. Bringing alerting up first costs nothing and removes that window.
- [ ] `wal_level=logical` and `max_slot_wal_keep_size=2GB` are confirmed **on the production
      database**, not assumed from the repo:
      ```bash
      docker exec odoo19-bct-postgres psql -U odoo -d bct -c \
        "SELECT name, setting FROM pg_settings WHERE name IN ('wal_level','max_slot_wal_keep_size');"
      ```
- [ ] `make warehouse-reader-check` passes: `SELECT` works, every write is denied.
- [ ] **`make check-dev-passwords` passes.** It asserts both directions: that
      `$BCT_DEV_USER_PASSWORD` authenticates *and* that Odoo's default `admin`/`admin` is refused.
      The second half is the one that matters — a check that only proves the documented credential
      works passes on a stack that accepts both, which is worse than one accepting only the default,
      because it looks configured. Verified able to fail: against an uninitialised database it exits
      **1**, unlike `make check-alerting` (§4).
      `make up-dev` applies the password as its last step, and `make seed-demo` passwords the demo
      users it creates, so on a fresh clone this should already be true.

      Note when testing by hand: the script sources `.env`, which **overrides** an inherited
      environment variable. `BCT_DEV_USER_PASSWORD=wrong make check-dev-passwords` therefore proves
      nothing — it silently uses the `.env` value. Use a bad `--db` to exercise the failure path.

---

## 4. Verify — read the numbers, do not accept "it started"

- [ ] All containers healthy: `docker compose -p odoo19-bct ps`
- [ ] The suite, against the production stack, excluding the destructive markers:
      ```bash
      bash tests/run.sh -m "not coldstart" -ra
      ```
- [ ] **Reconciliation specifically**, because it is the one that catches a warehouse that is wrong
      rather than merely down: `bash tests/run.sh -k reconciliation -s`
- [ ] **Live sync specifically**, including the delete leg: `bash tests/run.sh -k live_sync -s`
- [ ] Cross-tenant 403 returns exactly the contract-02 body: `bash tests/run.sh -k cross_tenant`
- [ ] Every mart reports `is_stale = false` for an active tenant:
      ```bash
      docker exec odoo19-bct-warehouse-db psql -U warehouse_admin -d warehouse -c \
        "SELECT count(*) FILTER (WHERE is_stale) AS stale, count(*) FROM warehouse.mart_freshness;"
      ```
- [ ] The four warehouse roles are what they should be — and in particular that the three
      non-superuser ones really are non-superuser, because RLS is not evaluated otherwise:
      ```bash
      bash tests/run.sh -k role_model -s
      ```
- [ ] Prometheus has the replication-slot series and the four alert rules are loaded.
- [ ] Alertmanager has a route that reaches a human. **[SECURITY OWES]** — the receiver
      configuration and the on-call destination are Security's to specify. An alert that fires into
      a channel nobody reads is worse than no alert, because it manufactures the belief that
      something is watching.
- [ ] **`make check-alerting` passes** (exit 0, and not the 77 it returns when it skips). It now
      probes Alertmanager directly rather than trusting Prometheus's `/api/v1/alertmanagers`, which
      under `static_configs` reports the configured target whether or not anything is listening.
      Verified able to fail: with Alertmanager stopped it exits non-zero with
      "1 configured, NONE answering". If Alertmanager is deliberately not on `127.0.0.1:39093`, set
      `ALERTMANAGER_URL` rather than ignoring the failure.
- [ ] **Still NOT PROVEN: that alerting comes back after a cold start.** The overlay is not brought
      up by `make up-dev` or `make up-analytics`, and the assertion that would prove it end to end
      has not yet run inside a cold-start execution. Do not record §6's alerting item as satisfied
      on the strength of a green `check-alerting` on a warm stack.

---

## 5. Post-deploy watch

- [ ] Watch retained WAL for the first hour. It should stay in kilobytes:
      ```bash
      watch -n 30 "docker exec odoo19-bct-postgres psql -U odoo -d bct -tAc \
        \"SELECT slot_name, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) \
          FROM pg_replication_slots;\""
      ```
- [ ] Confirm `warehouse.pipeline_state.last_success_at` keeps advancing on an idle system. It is
      driven by a 15 s heartbeat timer, so a value older than ~20 s means the loader is stuck, not
      that nothing happened.
- [ ] Take one deliberate end-to-end measurement rather than trusting the local numbers: create,
      update and delete one record, and check it lands. `bash tests/run.sh -k live_sync -s` does
      exactly this and prints the latencies.

---

## 6. Rollback

**Not yet demonstrated. [SECURITY OWES]** — the phase-5 acceptance criterion is a *demonstrated*
rollback, and this document must not imply one has happened. What follows is the manual shape.

- **Application rollback**: redeploy the previous image digest. Odoo module *downgrades* are not
  supported by Odoo; if the deploy ran a module upgrade with a schema migration, rolling the image
  back does not roll the database back, and the restore below is the only path.
- **Odoo rollback**: `make tenant-restore TENANT=bct FROM=backups/bct/<stamp>`.
- **Warehouse rollback**: usually unnecessary. The warehouse is *rebuildable* — drop the landing
  zone, re-provision the slot, re-run the backfill, `make dbt-run`. That is often faster and always
  more trustworthy than restoring a dump.
- **After any Odoo restore, treat the warehouse as poisoned and re-seed it.** This is not caution.
  If the restored database reuses primary keys, the latest-version-per-key projection **silently
  merges two different entities into one row** — a wrong answer that reconciles, which is worse than
  a failure. `docs/runbooks/analytics-pipeline.md` §3.5 is the procedure, including that every
  tenant must be truncated, not just the one you restored.

---

## 7. What this checklist does not cover

Stated so nobody assumes otherwise:

- **Automated deployment.** There is no `cd.yml`. Every step above is run by a person.
- **Image signing and provenance.** Planned in the phase-5 brief; **[SECURITY OWES]**.
- **DSAR erasure.** Manual; `docs/pdp-compliance.md` §5.4.
- **Cold-start verification on the production host.** `make test-coldstart` exists and is written,
  but it destroys the stack it runs against, so it belongs on a staging host or a pre-production
  window — never as a step in a deploy to a live one.
- **Multi-node or HA anything.** Single VPS, by design; ADR 0001 sized for it explicitly.
