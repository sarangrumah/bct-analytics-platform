# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries describe what was **built and verified**, not what was planned. Where something is a known
gap it appears under *Known gaps* rather than being omitted.

## [Unreleased] — `feat/analytics-platform`

Greenfield build of an Odoo 19 platform and a live analytics warehouse. The operator overrode the
original brief's "existing 162-addon platform" premise on 2026-08-31 and chose greenfield with a
five-addon domain set; `docs/agents/PLAN.md` records that deviation and its consequences.
On 2026-09-01 the operator reversed the addon half of that decision and the suite was imported
after all; `docs/adr/0002-addon-import.md` records that reversal, so the two entries below are
not in conflict — they are consecutive decisions.

### Added — the ATHERA platform (2026-09-01 / 2026-09-02)

The operator supplied a concept diagram named ATHERA — a company with three products, a public
site, a super-admin console and a per-client environment layer. The word appears nowhere in this
repository, so `docs/athera` does not exist and `docs/architecture.md` §8.1 carries the mapping
from each diagram node to the code that serves it.

**The finding that shaped all of it.** ADR 0002 imported the Odoo modules that CALL a set of
backend services without importing the services. `custom_super_admin`, `custom_hub_console` and
`custom_tenant_infra` had been calling `http://tenant-orchestrator:8080` into nothing;
`custom_ai_bridge` and `custom_ai_features` the same for an AI gateway. Separately,
`login-gateway`, `semantic-api` and `cdc` had working code, Dockerfiles and reserved ports but
appeared in no compose file — and two of them were run by shell scripts while being **built by
nothing**, so a fresh clone could not start them.

- **Four product stacks** under `compose/`: `odoo`, `insight`, `platform`, `agent`, plus
  observability. Split by product so one can move to its own VPS; the mechanism that actually
  makes that possible is env-driven inter-service URLs, not the file layout.
- **`tenant-orchestrator`** — the control-plane API those three modules were calling. Its entire
  privilege is one Postgres role and one Odoo login. **No docker socket**: the upstream platform
  repo mounts one, which would make a network-facing service root on the host.
- **`custom_athera_provisioner`** — builds a tenant database by spawning `odoo -d … -i …` as a
  child process inside the Odoo container. Odoo's RPC route is closed by
  `@check_db_management_enabled` while `list_db` is False, and it stays False.
- **Control plane** — `tenant_registry` (clients, plans, entitlement, hash-chained audit) and
  `cms` (the public site's content) in the admin Odoo database. `is_active()` is the single
  implementation of the "Active?" decision; the gateway, the orchestrator and the console all
  consult it.
- **`is_super_admin` / `subscription_active` / `products` claims**, minted on every login **and
  every refresh**, each defaulting to the denying value.
- **`hub-portal`** — Super Admin CMS, gated on `is_super_admin`, signing to the orchestrator
  server-side.
- **`ai-gateway`** — ATHERA Agent. `/v1/workflow/nlq` returns an Odoo **domain, never SQL**, and
  the returned plan is re-validated against the caller's own schema before it leaves the process.
- **`marketing-site`** — the public site, pages in `cms.page`, typed blocks and no
  `dangerouslySetInnerHTML` anywhere.
- **`caddy`** — the single entry point. Not decoration: a `dbfilter` naming two databases was
  measured to serve **neither**, and the Host header is the only per-request way to say which.
- **A second and third client** — `acme` and `gentle`, with different plans and different
  entitlements, `gentle` provisioned end to end through the signed API.
- **`import-policy`** — column classification for an Insight client who is not on Odoo.
- **Five skills** in `.claude/skills/athera-*`.

### Fixed — found by measuring, not by reasoning

- **Widening `ODOO_DBFILTER` to `^(bct|athera_admin)$` served NEITHER database.** Odoo cannot
  choose, so `/web/login` answered `303 → /web/database/selector`, which `list_db=False`
  disables. Only `^%d$` behind a proxy works.
- **Odoo with `proxy_mode` prefers `X-Forwarded-Host` over `Host`.** Rewriting only `Host` left
  the admin route on 303 while a direct request with the same header answered 200.
- **`dbfilter` applies to JSON-RPC too**, which took the login gateway down with
  `upstream_unavailable` on a correct password until `compose/odoo.yml` gained a network alias
  per served database.
- **`LOGIN_GATEWAY_ODOO_URL` was one fixed hostname**, so every login authenticated against
  `bct` whatever database was asked for. It is now a `{db}` template.
- **`read_session_claims` read a field only `custom_operating_unit` provides**, turning a correct
  super-admin password into a 503 on a database that does not install it. Its absence is now an
  answer, not an error.
- **`queue_job` runs jobs inside an HTTP request**, so `limit_time_real` applies and installing
  `website` into another database dies in its own data file. Provisioning shells out instead.
- **`odoo -i` leaves admin on Odoo's default password**, producing a tenant that provisioned
  cleanly, reported success and could not be logged into.
- **`action_log.tenant_id` had `ON DELETE SET NULL`** against a table whose append-only trigger
  refuses every UPDATE — so a tenant that had ever been acted on could not be deleted at all.
- **`anthropic==0.75.0` does not accept `fallbacks`**, raising `TypeError` at request time and
  turning every call into a 500. Support is detected once at construction.
- **`new URL(path, request.url)` in a route handler resolves against the BIND address**, so the
  console's login answered `Location: http://0.0.0.0:3000/`.
- **`[...slug]` does not match `/`**, so the marketing site's home page 404'd while every other
  page rendered.
- **`ODOO_INIT_MODULES` named five modules while the live database had 332**, so `make down-hard`
  would have silently returned the platform to a five-module stack.

### Known gaps — the ATHERA platform

- **The live Anthropic call has never run.** No API key on the build machine, and the operator has
  chosen to leave it unset. HMAC both ways, the tenant fence (10 tests), the schema refusals, the
  501s and the no-credential error path are all exercised; the model call itself is not.
- **`/v1/workflow/anomaly` and `/v1/workflow/classify` answer 501**, as do backups through the
  orchestrator. `custom_ai_features` calls the first two and `scripts/tenant-backup.sh` already
  does the third correctly on the host.
- **`gentle` is a registry row with no network alias and no Caddy block.** Provisioned through the
  API as the orchestrator's acceptance test and deliberately left unreachable; reachability is a
  separate reviewed step.
- **78 ruff findings across the imported addons are accepted as dated debt** to 2027-03-31, scoped
  to `addons/` with the reasoning in `.pre-commit-config.yaml`. B023 (20) is a verified false
  positive; the other 58 are real.
- **Per-client dbt models and metrics for a non-Odoo Insight source are bespoke work per client.**
  `import-policy` classifies their columns; the marts are the engagement.

### Added — addon suite (ADR 0002)

- **149 modules imported** from `sarangrumah/odoo-platform` into `addons/<group>/`,
  reproducibly, by `scripts/import-platform-addons.py`. Waves are slices of the
  dependency graph by topological layer, not tiers: `addons/core/` holds modules at
  layers 0 through 4, so importing by tier leaves dangling dependencies. 332 modules
  install; the registry loads; every declared dependency resolves.
- **`docs/module-catalog.md` + `.csv`** — the 154-module inventory on five axes
  (tier, domain, measured maturity, client coupling, disposition), plus the
  duplication findings, the custom-vs-core field audit and the CDC impact per
  module. Generated by `tools/module_inventory.py` in the platform repo, on top of
  that repo's existing `module_diff.py` rather than a new scanner.
- **`custom_accounting_menu`** — splits Odoo CE's single "Invoicing" application
  into Invoicing (Dashboard, Customers, Vendors) and Accounting (Entries, Review,
  Reporting, Configuration, and the suite's nine tax and ledger menus). Menus are
  re-parented by XML ID, so the 75 items the accounting modules contribute follow
  their parents untouched.
- **241 generated search views** across 92 modules, by
  `scripts/generate-search-views.py`, from live `ir_model_fields` introspection.
  285 of the imported models had no search view, which in Odoo means no filters, no
  Group By, and a search box that only looks at `name`. `make verify` now fails if
  any custom model with a table lacks one.
- **`odoo/requirements.txt`** — eight Python packages the addon suite declares and
  the base image does not carry, pinned with `==` and a hash, installed
  `--require-hashes --no-deps`. The build asserts the imports rather than trusting
  pip's exit code.
- **`CORETAX_SERTEL_MASTER_KEY`** generated by `scripts/gen-env-secrets.py` and
  passed through compose. Without it `custom_core`'s encrypted-parameter helper
  raises and `res.partner.create` fails outright.

### Added — de-branding and its migration

- **No customer name remains in `addons/`** — prose, module directories, model
  names, field names, XML IDs and Python class names. 20 models, 5 fields, 7 module
  directories and 655 XML IDs moved. Three rule shapes were needed: letter
  boundaries (so `claim` is not a sighting and `we aim to` survives), CamelCase
  matched on the following capital (`LevisCategReclass`), and UPPERCASE identifiers
  matched on the trailing underscore (`AIM_COMPANY`, which the prose rule turned
  into `the tenant_COMPANY` — a SyntaxError Odoo only reported at import).
- **`scripts/migrate-client-renames.py`** — renames modules, models, tables,
  columns and XML IDs in an existing database so it matches the de-branded tree.
  Odoo renames none of those on upgrade. It imports the substitution table from the
  import script rather than copying it, and every statement is guarded on the old
  name still existing, so it is idempotent.
- **`--verify-python`** on the import script: all 1,701 imported files must still
  compile. The scrub rewrites identifiers, not only prose, so a rule that is right
  for a sentence can be wrong for a constant.

### Changed

- `addons_path` gains the eight group directories, bare mount first so this repo's
  own five modules keep their paths and win any name collision.
- `server_wide_modules` gains `queue_job`: nine modules dispatch jobs through it.
- `warehouse_ctl.py gen-raw-ddl` now emits `ALTER TABLE ... ADD COLUMN IF NOT
  EXISTS` as well as `CREATE TABLE IF NOT EXISTS`, and prunes orphans. A source
  column that is renamed or removed used to stay in `raw.*` with no policy row,
  which contract 01 makes a hard failure — twelve of them appeared after the
  de-branding rename. An empty orphan is dropped, cascading only through dbt's own
  `staging`/`marts` views because dbt rebuilds those; an orphan with data is never
  dropped automatically, it is reported and the command exits non-zero. The create was a no-op on an
  existing landing table, so a source column added later never reached `raw.*`;
  installing the suite took `res_company` from 121 classified columns to 186 and
  the loader's INSERT then named columns the table did not have.
- `scripts/check-gitignore.py` walks the nested tree. It globbed one level, so
  after the import it was checking 5 modules and blind to the other 149 — the exact
  silent omission it exists to catch. It now covers 155.
- `scripts/scan-secrets.py` no longer fires on values that are deliberately wrong
  (`not-the-`, `wrong-`, `dummy-`, …). The pattern was narrowed rather than the
  files exempted, so a real credential pasted into a test is still caught.
- The classification seed grew from 740 to 1,114 columns across the same 17 models.

### Known gaps

- `custom_arka_aim_seed` and `custom_storefront_api` are present but not installed;
  ADR 0002 §6 records why. The second is the more interesting: an addon that turns a
  stored field into a computed one silently breaks logical replication of that
  column.
- CI still has no addon install gate: `--test-enable` never runs on a runner, and
  the search-view gate needs a live database, so it lives in `make verify`.
- `addons/` still reaches only the dev overlay. Neither the base compose file nor
  `cd.yml` mentions it, so the suite runs in development only.

### Added — platform

- **Odoo 19 CE stack** (`docker-compose.yml`, `docker-compose.dev.yml`), Postgres 16 with
  `wal_level=logical` and `max_slot_wal_keep_size=2GB` set at first boot, Redis 7. All images
  digest-pinned. Every compose invocation scoped to project `odoo19-bct`.
- **Five addons**: `custom_pdp_core` (the 698-column classification registry), `custom_pdp_masking`
  (the HMAC specification, in-UI masking and the `export_data` path), `custom_operating_unit`
  (fail-closed OU record rules), `custom_ppob`, `custom_demo_seed` (12 months across 2 operating
  units). 94 addon tests, exit 0.
- **`Makefile`** as the single entry point; a `RESERVED` block names the target namespaces each
  agent may claim, because `make` silently takes the last definition of a duplicated target.
- **Observability overlay**: Prometheus, Alertmanager, Grafana, Loki, promtail, node and postgres
  exporters, plus replication-slot alert rules keyed to ADR 0001's thresholds.

### Added — analytics

- **ADR 0001**: Postgres-native marts fed by native `pgoutput` logical replication, transformed by
  dbt-core. ClickHouse considered and rejected on SCD2 support, CDC path maturity and RLS
  idiomaticity, not on image size.
- **Warehouse** (`analytics/warehouse/`): schemas `raw`/`staging`/`marts`/`warehouse`/`snapshots`,
  four roles, and the contract-05 metadata tables — `column_policy`, `pipeline_state`,
  `tenant_registry`, `mart_sla`, the `mart_freshness` view and `log_access()`.
- **CDC loader** (`analytics/cdc/`): resumable keyset backfill, then a `pgoutput` stream. Masking is
  applied **during** load. Deletes land as tombstones. The LSN is confirmed only after the warehouse
  transaction commits, so Postgres is never told it may discard WAL the warehouse has not stored.
- **dbt project** (`analytics/dbt/`): 16 marts, SCD2 snapshots for `dim_partner` and `dim_product`,
  and a `post-hook` applying `ENABLE` + `FORCE ROW LEVEL SECURITY` to every model so it survives
  `--full-refresh`.
- **`login-gateway`**: authenticates against Odoo over JSON-RPC, issues RS256 JWTs, publishes JWKS
  with two distinct keys for rotation.
- **`semantic-api`**: `POST /v1/query` compiled from the metric contract. No raw SQL is accepted.

### Added — tests (`tests/`, 71 tests)

Integration tests exercising the seams between components, runnable with `make test`.

- **Live sync with real timestamps** — create → update → **delete** through the Odoo ORM, asserted
  end to end in the warehouse, including that the delete disappears from the latest-non-deleted
  projection. This is the test that distinguishes a live mart from a nightly dump.
- **Reconciliation** — warehouse totals against Odoo, per table, per day, plus the debit==credit
  identity and stock quantity.
- **Idempotency** — a second load over the same range, asserted to change neither the live
  projection *nor* the landing-zone row count.
- **Masking** — asserted against the actual stored value, with the digest re-derived in the test
  from the specification rather than imported from the loader, plus a `sha256(salt||value)` negative
  control.
- **Tenant isolation** — RLS at the storage layer, asserting the connection's own identity first,
  and asserting that the other tenant *has* rows so the zero is evidence rather than absence.
- **Cross-tenant 403** — the contract-02 body asserted character for character, and identical for a
  tenant that does not exist.
- **Token abuse** — tampered signature, `alg:none` and HS256-substitution hand-assembled from
  base64url segments rather than minted with a library that refuses to produce them.
- **Freshness** — asserting that `last_success_at` *stops* when the pipeline stops, which is the
  half a clock-driven implementation would fail.
- **Slot-lag alerts** — `promtool test rules`, including the below-threshold negative cases.
- **Backfill resumability** — `SIGKILL` mid-run, then resume, asserting the byte-identical result.
- **Clone verification** — every claim about installability made against a `git clone` of the
  branch, never the working tree.

### Added — documentation

- `docs/architecture.md` — what was built, with the data path from Odoo's WAL to a dashboard pixel.
- `docs/runbooks/analytics-pipeline.md` — dropped-slot recovery, re-seeding, a restored-source
  database, reconciliation triage, and what each alert means.
- `docs/pdp-compliance.md` — UU 27/2022 position, stating **in those words** that DSAR erasure
  propagation is a manual runbook and not automated.
- `docs/cicd-activation.md`, `docs/prod-deploy-checklist.md` — activation and deploy procedures,
  with the sections Security owns marked rather than invented.
- `docs/adr/`, `docs/agents/` — decisions, contracts and the plan (Lead-owned).

### Fixed

- **`allowed_ou: []` reversed to mean *no* Operating Units** (contract 02, GATE 3 amendment). The
  contract as frozen said "all" while the producer's record rules said "none", so the same token
  would have shown a user with no entitlement *more* in the dashboard than in Odoo — a privilege
  escalation manufactured purely by two documents disagreeing. The bypass is now an explicit
  `all_ou` boolean, and an absent claim grants nothing.
- **`export_data` masking bypass** — a user with `base.group_allow_export` but without the PDP
  viewer group received cleartext; now receives the masked value.
- **`.gitignore` patterns anchored**, after an unanchored `data/` silently excluded three
  install-critical files — including the entire 724-row classification seed — so a fresh clone could
  not install those modules while every working-tree test passed. `make check-gitignore` now guards
  it, and `tests/test_12_clone_install.py` verifies from a clone.
- **Freshness heartbeat moved out of the message callback** into a timer thread. It had been
  reachable only when a message arrived, so `last_success_at` aged on a healthy but idle pipeline —
  measured at 76 s and rising against a 60 s PPOB SLA. Now bounded at ~15 s.
- **Slot invalidation made fatal.** The monitor previously only logged it, leaving the consumer
  running against a slot whose WAL Postgres had already discarded — producing a mart with a hole and
  no error anywhere.
- **`--reload` removed** rather than repaired: it crashed on every invocation
  (`AttributeError: … 'clear_completion'`) and belonged to a design superseded when the resume point
  moved into the landing zone. Re-running `--backfill-only` is the supported path, and
  `test_cli_flags.py` now asserts every advertised flag is actually dispatched.

### Security

- **Read-only by construction.** The pipeline connects to Odoo as `warehouse_reader`, holding only
  `SELECT` and `REPLICATION`. `INSERT`, `UPDATE`, `DELETE`, `CREATE TABLE` and `CREATE TEMP TABLE`
  are all denied — verified with pasted denials, not asserted.
- **Append-only by grant.** `warehouse_loader` holds no `UPDATE`, `DELETE`, `TRUNCATE` or `CREATE`,
  so the landing-zone rules are enforced by the database rather than trusted to the loader's code.
- **Four warehouse roles**, of which only `warehouse_admin` is a superuser, and nothing queries data
  as it. A single shared identity would have made every isolation test in the project pass and mean
  nothing, because RLS is never evaluated for a `SUPERUSER` or `BYPASSRLS` role.
- **Storage-layer tenant isolation**: 13,755 rows belonging to a second tenant exist across the
  marts, and a session scoped to the first saw none of them.
- CI: pre-commit, semgrep, gitleaks over full history, hadolint, trivy image and filesystem scans,
  `pip-audit` and SBOM generation, gated by a single `ci-gate` check.

### Known gaps

- **DSAR erasure is a manual runbook, not automated.** `docs/pdp-compliance.md` §5 states this
  explicitly and gives the procedure. Recorded as a gap against UU 27/2022 Pasal 8 and 16(1)(f),
  not presented as a design choice. The most consequential entry here.
- **`warehouse.access_audit.application_name` is NOT on the wire.** Contract 05 §A.6 makes it a
  MUST, because `warehouse_rls` is shared between `semantic-api` and `warehouse-exporter` and
  `usename` cannot say which service performed a read. Measured: three live `warehouse_rls`
  connection groups carry no `application_name`.
  `tests/test_05_tenant_isolation.py::test_access_audit_names_the_service_that_read` is written and
  **skips with that reason**, becoming a real assertion the moment both images are rebuilt.
- **Four warehouse alert rules are dark on any stack whose last dbt invocation excluded tests.**
  `make dbt-run` is `dbt build --exclude-resource-type test` and the exporter scopes to the single
  most recent invocation, so `WarehouseReconciliationFailing`, `WarehouseDbtTestFailing`,
  `WarehouseBuildStale` and `WarehouseTestsNotRunning` have no samples until `make dbt-test` runs.
  Measured in both directions: 0 series before, then 7 / 2 / 2 / 2 after. Whether the Makefile
  should chain the two is Platform-Infra's call.
- **`ARGS` leaks through `MAKEFLAGS` into every nested `make`.** `make test-coldstart ARGS="-s"`
  passed `-s` down to `make seed-demo`, whose script refused it, and four tests then failed on the
  empty database that left behind. Proven with a two-line makefile. This suite now passes an
  explicit `ARGS=` to every nested call; the Makefile-side fix is Platform-Infra's.
- **CD has never executed against a real remote**, because there is no git remote. Rollback is
  demonstrated; the deploy path itself is the manual `docs/prod-deploy-checklist.md`.
- **The warehouse backup/restore *green* round trip is NOT PROVEN.** The failure direction is
  proven; the `--into` rehearsal is not implemented.
- **The Grafana dashboard has never been opened in a browser.** Panels are provisioned and their
  queries are exercised through the exporter, but no human or test has looked at a rendered page.
- **The cold start has not been re-executed since its ordering was corrected.** Run 3 was 9/11 with
  both failures traced to one ordering mistake in the test — `up-analytics` ran before `seed-demo`,
  contradicting `docs/prod-deploy-checklist.md` §3 — and the fix is demonstrated in both directions
  but not yet re-run end to end. Command:
  `BCT_COLDSTART=i-understand-this-destroys-the-bct-oltp-data ASSUME_YES=1 make test-coldstart`
- **Semantic audit cannot be made mandatory** inside Postgres: there is no `SELECT` trigger and
  `postgres:16-alpine` does not ship `pgaudit`. `log_statement='all'` on the serving role is the
  compensating control, and it is applied by the server so a client cannot opt out.
