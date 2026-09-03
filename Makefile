# ===========================================================================
# odoo19-bct — developer entry points.
#
#   make                 same as `make help`
#   make dev-bootstrap   one-time setup on a fresh clone
#   make up-dev          bring the stack up and leave /web/login answering 200
#
# EVERY docker compose invocation in this file is scoped with -p $(PROJECT).
# This host also runs odoo19-platform-*, odoo19-analytics-* and
# smart-warga-postgres-1. An unscoped `docker compose down` or a
# `docker system prune` would hit them, and their data is not recoverable from
# here. There is no target in this file that can touch another project.
#
# Recipes are shell, so lines are tab-indented. See .editorconfig.
# ===========================================================================

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
.ONESHELL:

PROJECT      ?= odoo19-bct

# ---------------------------------------------------------------------------
# COMPOSE FILES — one per PRODUCT, under compose/.
#
# The split is by product, not by concern, so that a product can later be
# moved to its own VPS by changing URLs in .env and nothing else:
#
#   compose/odoo.yml        ODOO       postgres, redis, odoo
#   compose/insight.yml     Insight    warehouse-db, cdc, semantic-api, portal
#   compose/platform.yml    shared     login-gateway (+ orchestrator, hub-portal,
#                                      caddy, marketing-site in later phases)
#   compose/agent.yml       Agent      ai-gateway (Phase 3)
#   compose/observability.yml          prometheus, grafana, loki, exporters
#
# --env-file is EXPLICIT and not optional. Compose looks for `.env` in the
# project directory, which since the move is compose/ — so without this flag
# every `${VAR}` silently resolves empty and required-variable interpolation
# fails. Passing it is cheaper than a `.env` living in two places.
# ---------------------------------------------------------------------------
ENVFILE      ?= .env

C_ODOO     := -f compose/odoo.yml -f compose/odoo.dev.yml
C_INSIGHT  := -f compose/insight.yml
C_PLATFORM := -f compose/platform.yml
C_AGENT    := -f compose/agent.yml
C_OBS      := -f compose/observability.yml

# COMPOSE_IGNORE_ORPHANS: a target that names only some of the project's files
# sees the rest of the project's containers as orphans and prints a warning on
# every `up`. It never removes them — that needs --remove-orphans, which no
# target here passes — but the warning trains people to ignore compose output,
# which is how a real message gets missed.
COMPOSE := COMPOSE_IGNORE_ORPHANS=true docker compose -p $(PROJECT) --env-file $(ENVFILE)

DC          := $(COMPOSE) $(C_ODOO)
DC_INSIGHT  := $(COMPOSE) $(C_ODOO) $(C_INSIGHT)
DC_PLATFORM := $(COMPOSE) $(C_ODOO) $(C_PLATFORM)
DC_AGENT    := $(COMPOSE) $(C_ODOO) $(C_AGENT)
DC_OBS      := $(COMPOSE) $(C_ODOO) $(C_OBS)

# Every stack at once. `down` uses this: it previously used DC_OBS, which does
# not name the insight or platform files, so a `make down` reported success
# while leaving warehouse-db, semantic-api, cdc and the portal running.
DC_ALL := $(COMPOSE) $(C_ODOO) $(C_INSIGHT) $(C_PLATFORM) $(C_AGENT) $(C_OBS)

# Optional argument variables, documented per target:
#   TENANT=<slug>   MODULES=<a,b>   FROM=<backup dir>   INTO=<slug>   SERVICE=<name>
TENANT  ?=
MODULES ?=
FROM    ?=
FILE    ?=
INTO    ?=
SERVICE ?=
ARGS    ?=

# python3, not python: the Windows hosts here have no `python` on PATH.
PYTHON  ?= python3
OUT     ?=

# ---------------------------------------------------------------------------
# help — the default target.
#
# Descriptions are parsed out of the `## ` comment after each target name, so a
# new target is self-documenting the moment it is written and cannot drift out
# of sync with a hand-maintained list.
# ---------------------------------------------------------------------------
.PHONY: help
help: ## Show this help (default target)
	@echo ""
	@echo "  odoo19-bct - Odoo 19 CE + Postgres 16 (wal_level=logical) + Redis 7"
	@echo "  project: $(PROJECT)   ports: 127.0.0.1 38069/38072/35432/36379"
	@echo ""
	@awk 'BEGIN {FS = ":.*?## "} \
	     /^# ==== / { printf "\n  \033[1m%s\033[0m\n", substr($$0, 8); next } \
	     /^[a-zA-Z0-9_-]+:.*?## / { printf "    \033[36m%-22s\033[0m %s\n", $$1, $$2 }' \
	     $(MAKEFILE_LIST)
	@echo ""
	@echo "  variables:  TENANT=<slug>  MODULES=<a,b>  FROM=<dir>  INTO=<slug>  SERVICE=<name>"
	@echo ""

# ==== Setup

.PHONY: dev-bootstrap
dev-bootstrap: ## One-time setup: generate .env, create addons/, verify ports and line endings
	@bash scripts/dev-bootstrap.sh

.PHONY: build
build: ## Rebuild the Odoo image (pinned digest; no cache reuse for apt layers)
	@$(DC) build odoo

.PHONY: config
config: ## Validate the merged compose configuration of EVERY product stack
	@$(DC_ALL) config -q && echo "CONFIG_OK"

# ==== Lifecycle

.PHONY: up-dev
up-dev: ## Start the odoo stack, initialise the database, wait for healthy
	@bash scripts/up-dev.sh

.PHONY: up
up: ## Start every product stack in dependency order, then report the URLs
	@bash scripts/up-all.sh

.PHONY: down
down: ## Stop and remove this project's containers (volumes are KEPT)
	@echo "scoped to project $(PROJECT) only — other stacks on this host are untouched"
	@$(DC_ALL) down --remove-orphans

.PHONY: down-hard
down-hard: ## DESTRUCTIVE: down + delete this project's volumes (all data lost)
	@echo ""
	@echo "  This deletes volumes $(PROJECT)_pgdata, _odoodata, _redisdata,"
	@echo "  _warehousedata and the observability volumes."
	@echo "  Every database and every filestore in project $(PROJECT) is lost."
	@echo "  Other projects on this host are NOT affected."
	@echo ""
	@echo "  The rebuilt stack installs \$$ODOO_INIT_MODULES and nothing else."
	@echo "  Anything installed by hand since the last cold start is gone with it."
	@echo ""
	@read -r -p "  Type the project name to confirm: " reply; \
	 if [ "$$reply" = "$(PROJECT)" ]; then \
	     $(DC_ALL) down -v --remove-orphans; \
	 else \
	     echo "  aborted."; exit 1; \
	 fi

.PHONY: restart
restart: ## Restart services (SERVICE=<name> for one, default all)
	@$(DC) restart $(SERVICE)

.PHONY: ps
ps: ## Show this project's containers and health, across every product stack
	@$(DC_ALL) ps

.PHONY: logs
logs: ## Follow logs (SERVICE=<name> for one, default all)
	@$(DC_ALL) logs -f --tail=200 $(SERVICE)

.PHONY: stats
stats: ## One-shot memory and CPU usage for this project's containers
	@docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}' \
	    $$($(DC_ALL) ps -q 2>/dev/null) 2>/dev/null || \
	 docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}'

# ==== Control plane (ATHERA Phase 2)

.PHONY: control-plane
control-plane: ## Create the admin database + tenant_registry schema (idempotent)
	@bash scripts/control-plane-apply.sh

.PHONY: up-orchestrator
up-orchestrator: ## Start the tenant-orchestrator (the control-plane API)
	@$(DC_PLATFORM) up -d --build tenant-orchestrator
	@echo "orchestrator  http://127.0.0.1:$${ORCHESTRATOR_HOST_PORT:-38300}   (every /v1/* route is HMAC-signed)"

.PHONY: up-console
up-console: ## Start the Super Admin CMS (requires an is_super_admin session)
	@$(DC_PLATFORM) up -d --build hub-portal
	@echo "hub-portal    http://127.0.0.1:$${HUB_PORTAL_HOST_PORT:-33003}   (https://admin.athera.localhost through caddy)"

.PHONY: up-site
up-site: ## Start the public ATHERA site (content comes from the control-plane DB)
	@$(DC_PLATFORM) up -d --build marketing-site
	@echo "marketing-site http://127.0.0.1:$${MARKETING_SITE_HOST_PORT:-33002}   (https://athera.localhost through caddy)"

.PHONY: up-agent
up-agent: ## Start ATHERA Agent (ai-gateway; the LLM provider comes from .env)
	@$(DC_AGENT) up -d --build ai-gateway
	@echo "ai-gateway    http://127.0.0.1:$${AI_GATEWAY_HOST_PORT:-38400}   (every /v1/* route is HMAC-signed)"

.PHONY: control-plane-status
control-plane-status: ## Show the registry: tenants, plans, and the audit chain's integrity
	@# Reports; never fails. A status command that exits non-zero because the
	@# thing is not built yet is a status command people stop running.
	@$(DC) exec -T postgres psql -U $${POSTGRES_USER:-odoo} -d $${ATHERA_ADMIN_DB:-athera_admin} 	    --no-psqlrc -P pager=off -c 	    "SELECT slug, state, plan_code, insight_source_kind, tenant_registry.is_active(slug) AS active, tenant_registry.entitlements(slug) AS products FROM tenant_registry.tenants ORDER BY id" 	    2>/dev/null || echo "  tenant_registry not present in $${ATHERA_ADMIN_DB:-athera_admin}  (make control-plane)"
	@$(DC) exec -T postgres psql -U $${POSTGRES_USER:-odoo} -d $${ATHERA_ADMIN_DB:-athera_admin} 	    --no-psqlrc -tA -c 	    "SELECT '  audit chain: ' || CASE WHEN count(*) = 0 THEN 'intact' ELSE count(*) || ' BROKEN LINK(S)' END FROM tenant_registry.verify_action_chain()" 	    2>/dev/null || true

# ==== Database

.PHONY: init-db
init-db: ## Create and initialise the default Odoo database (idempotent)
	@bash scripts/init-db.sh $(if $(MODULES),--modules $(MODULES),)

.PHONY: install-modules
install-modules: ## Install/upgrade modules: make install-modules MODULES=custom_pdp_core
	@test -n "$(MODULES)" || { echo "MODULES is required, e.g. MODULES=custom_pdp_core,custom_ppob"; exit 1; }
	@bash scripts/init-db.sh --modules "$(MODULES)" --force

.PHONY: set-dev-passwords
set-dev-passwords: ## Apply $$BCT_DEV_USER_PASSWORD to admin + the demo users (idempotent)
	@bash scripts/set-dev-passwords.sh $(if $(TENANT),--db $(TENANT),)

.PHONY: check-dev-passwords
check-dev-passwords: ## Assert the dev password logs in AND that Odoo's default 'admin' is refused
	@bash scripts/set-dev-passwords.sh --check $(if $(TENANT),--db $(TENANT),)

.PHONY: seed-demo
seed-demo: ## Generate the demo volume (custom_demo_seed) and password its users; idempotent
	@bash scripts/seed-demo.sh $(if $(TENANT),--db $(TENANT),) $(ARGS)

.PHONY: psql
psql: ## Open a psql shell as the odoo superuser (TENANT=<slug> for another database)
	@$(DC) exec postgres psql -U odoo -d $(if $(TENANT),$(TENANT),$${ODOO_DB_NAME:-bct})

.PHONY: shell
shell: ## Open an Odoo shell (ORM REPL) against the default database
	@$(DC) exec odoo odoo shell -d $${ODOO_DB_NAME:-bct} --no-http

.PHONY: sh
sh: ## Open a plain shell in a container (SERVICE=odoo|postgres|redis)
	@$(DC) exec $(if $(SERVICE),$(SERVICE),odoo) bash 2>/dev/null || \
	 $(DC) exec $(if $(SERVICE),$(SERVICE),odoo) sh

# ==== Tenants

.PHONY: tenant-provision
tenant-provision: ## Create a tenant database: make tenant-provision TENANT=acme
	@test -n "$(TENANT)" || { echo "TENANT is required, e.g. make tenant-provision TENANT=acme"; exit 1; }
	@bash scripts/tenant-provision.sh "$(TENANT)" $(if $(MODULES),--modules $(MODULES),) $(ARGS)

.PHONY: tenant-backup
tenant-backup: ## Back up a tenant's DATABASE and FILESTORE: make tenant-backup TENANT=bct
	@test -n "$(TENANT)" || { echo "TENANT is required, e.g. make tenant-backup TENANT=bct"; exit 1; }
	@bash scripts/tenant-backup.sh "$(TENANT)" $(ARGS)

.PHONY: tenant-restore
tenant-restore: ## Restore a tenant: make tenant-restore TENANT=bct FROM=backups/bct/<stamp> [INTO=copy]
	@test -n "$(TENANT)" || { echo "TENANT is required"; exit 1; }
	@test -n "$(FROM)"   || { echo "FROM is required, e.g. FROM=backups/bct/20260831T041500Z"; exit 1; }
	@bash scripts/tenant-restore.sh "$(TENANT)" "$(FROM)" $(if $(INTO),--into $(INTO),) $(ARGS)

# ==== Verification

.PHONY: warehouse-reader-check
warehouse-reader-check: ## Prove warehouse_reader can SELECT and replicate but cannot write
	@bash scripts/warehouse-reader-check.sh $(if $(TENANT),--db $(TENANT),)

.PHONY: check-gitignore
check-gitignore: ## Fail if .gitignore would drop an addon data file or a dbt seed
	@python3 scripts/check-gitignore.py

.PHONY: check-alerting
check-alerting: ## Fail if a scrape target is down, Alertmanager is absent, or a rule can never fire
	@$(PYTHON) scripts/check-alerting.py

.PHONY: scan-secret
scan-secret: ## Fail if a real secret is committed, or .env.example drifts off `changeme`
	@python3 scripts/scan-secrets.py

.PHONY: verify
verify: ## Run every Phase 1 acceptance check and print the evidence
	@bash scripts/verify.sh

# ==== Tests

.PHONY: test
test: ## Run the integration suite in tests/ (ARGS='-k live' to filter)
	@bash tests/run.sh $(ARGS)

.PHONY: test-coldstart
test-coldstart: ## DESTRUCTIVE: cold-start suite; scoped to THIS project, other stacks checked after
	@bash scripts/coldstart-guard.sh $(ARGS)

.PHONY: metric-fixtures
metric-fixtures: ## Generate the semantic-api metric fixtures the Frontend agent builds against
	@$(PYTHON) scripts/analytics/metric-fixtures.py $(ARGS)

# ==== Observability

.PHONY: up-obs
up-obs: ## Start Prometheus, Grafana, Loki, Alertmanager and the exporters
	@$(DC_OBS) up -d prometheus grafana loki promtail alertmanager postgres-exporter node-exporter
	@echo "grafana      http://127.0.0.1:$${GRAFANA_HOST_PORT:-33001}"
	@echo "prometheus   http://127.0.0.1:$${PROMETHEUS_HOST_PORT:-39090}"
	@echo "alertmanager http://127.0.0.1:$${ALERTMANAGER_HOST_PORT:-39093}"

.PHONY: down-obs
down-obs: ## Stop only the observability services (the base stack keeps running)
	@$(DC_OBS) stop prometheus grafana loki promtail alertmanager postgres-exporter node-exporter
	@$(DC_OBS) rm -f prometheus grafana loki promtail alertmanager postgres-exporter node-exporter

# ==== Analytics warehouse (Data Warehouse agent)
#
# Every target here is scoped -p $(PROJECT) through $(DC_ANALYTICS), like the
# rest of this file. Names are taken from the namespace reserved in
# docs/agents/contracts/04-platform.md 6 and collide with nothing above.

# Kept as an alias of DC_INSIGHT. The warehouse is one part of ATHERA Insight
# rather than a product of its own, so the stack it lives in is named for the
# product; this name stays because the runbooks and contracts use it.
DC_ANALYTICS := $(DC_INSIGHT)

# `run --rm --no-deps`, not `exec`: dbt is a batch job, not a service. Leaving a
# restart-policy container idling would burn ~150 MiB of the VPS budget for
# something that runs for twelve seconds. --no-deps because the compose
# dependency is only there to order a `up`, and re-checking warehouse-db health
# on every invocation adds a second to every command.
DBT := MSYS_NO_PATHCONV=1 $(DC_ANALYTICS) run --rm --no-deps dbt
WCTL := MSYS_NO_PATHCONV=1 $(DC_ANALYTICS) run --rm --no-deps --entrypoint python dbt /warehouse/bin/warehouse_ctl.py

.PHONY: up-analytics
up-analytics: ## Start the warehouse, apply its DDL, sync the PDP policy and load the landing zone
	@# PRECONDITION, and it is here because the failure without it is a
	@# psycopg2 traceback ending in "could not translate host name postgres",
	@# which reads like a warehouse bug and is not one. The policy sync, the
	@# raw DDL generator and the reconciliation FDW all read the OLTP database
	@# as warehouse_reader; none of them can do anything useful without it.
	@$(DC_ANALYTICS) ps --services --filter status=running | grep -qx postgres || { 		echo ""; 		echo "  The base stack is not running. up-analytics reads Odoo's Postgres"; 		echo "  as warehouse_reader to sync the PDP policy, generate raw.* and wire"; 		echo "  the reconciliation FDW, and it cannot do any of that without it."; 		echo ""; 		echo "      make up-dev"; 		echo ""; 		exit 1; 	}
	@$(DC_ANALYTICS) up -d warehouse-db warehouse-exporter
	@# RESTART, not just up. The exporter reads
	@# analytics/warehouse/exporter/queries.yml from a bind mount, and
	@# `up -d` only recreates a container when its DEFINITION changes - a
	@# changed mounted file is invisible to it. That gap already produced
	@# one alert (MartStalePage) whose selector matched zero series while
	@# promtool passed and Prometheus reported health=ok. Restarting on
	@# every bring-up costs a second and removes the whole class.
	@$(DC_ANALYTICS) restart warehouse-exporter >/dev/null
	@bash analytics/warehouse/bin/warehouse-apply.sh
	@$(DC_ANALYTICS) --profile tools build dbt
	@$(WCTL) sync-policy
	@$(WCTL) gen-raw-ddl
	@$(WCTL) gen-fdw
	@$(WCTL) load-fixture --tenant bct_t2
	@echo "warehouse    127.0.0.1:$${WAREHOUSE_HOST_PORT:-35433}  (db $${WAREHOUSE_DB:-warehouse})"

.PHONY: down-analytics
down-analytics: ## Stop only the analytics services (the base stack keeps running)
	@$(DC_ANALYTICS) stop warehouse-db warehouse-exporter
	@$(DC_ANALYTICS) rm -f warehouse-db warehouse-exporter

.PHONY: dbt-run
dbt-run: ## Build every dbt model (seeds, snapshots, staging, marts)
	@$(DBT) build --exclude-resource-type test

.PHONY: dbt-test
dbt-test: ## Run every dbt test, including the reconciliation against live Odoo
	@$(DBT) test

.PHONY: import-policy
import-policy: ## Load a non-Odoo client's column classification: make import-policy FILE=policies/x.csv
	@test -n "$(FILE)" || { echo "FILE is required, e.g. FILE=policies/acme.csv (relative to analytics/warehouse/)"; exit 1; }
	@$(WCTL) import-policy --file /warehouse/$(FILE)

.PHONY: dbt-docs
dbt-docs: ## Generate the dbt catalogue into analytics/dbt/target
	@$(DBT) docs generate

.PHONY: warehouse-backup
warehouse-backup: ## pg_dump the warehouse with a manifest and SHA256SUMS
	@bash analytics/warehouse/bin/warehouse-backup.sh $(if $(OUT),--out $(OUT),)

.PHONY: warehouse-restore
warehouse-restore: ## Restore a warehouse backup: make warehouse-restore FROM=backups/warehouse/<stamp>
	@test -n "$(FROM)" || { echo "FROM=<backup dir> is required"; exit 2; }
	@bash analytics/warehouse/bin/warehouse-backup.sh --restore $(FROM)

# ==== Backend service tier (login-gateway, semantic-api, CDC)
#
# These four names were RESERVED in the block below and never defined, so the
# whole Backend tier existed only as scripts a human had to know to invoke, in
# an order recorded nowhere. Backend ran cdc-provision.sh by hand; a fresh clone
# did not, and no target would have. A comment naming four targets that do not
# exist is worse than no comment - it reads as documentation of something that
# works, which is exactly how BCT_DEV_USER_PASSWORD came to look implemented.
#
# THE ORDER, which is the artefact that was actually missing. Taken from the
# scripts, not from prose:
#
#   make up-dev          Odoo + Postgres must be up; cdc-provision reads them.
#   make up-analytics    the warehouse must exist; the loader lands into it and
#                        cdc-provision builds its column list from
#                        warehouse.column_policy.
#   make up-gateway      BEFORE up-semantic. The API fetches JWKS from the
#                        gateway at SEMANTIC_API_JWKS_URL; started the other way
#                        round it cannot verify a token and returns 401 on every
#                        VALID login, which reads as a client-side auth failure.
#   make up-semantic
#   make cdc-start       runs cdc-provision.sh FIRST, then cdc-run.sh. That
#                        order is load-bearing and is enforced in the recipe:
#                        publication first, slot second. WAL retention starts
#                        the instant a slot exists and the 2 GB cap
#                        (ADR 0001) starts counting immediately, so a slot
#                        created before its consumer is ready is the exact
#                        failure the cap exists to bound.
#
# Every recipe invokes "bash scripts/analytics/x.sh", never "./x.sh". That is a
# deliberate answer to the exec-bit question: those six files are mode 100644,
# and this repository has already proven it cannot reliably RECORD that bit -
# core.fileMode=false means a mode change survives neither "git commit -- path"
# nor "-c core.fileMode=true", and the plumbing route leaves a pending revert in
# an index three agents share. Depending on a bit we cannot record would make a
# latent problem load-bearing on Linux. "bash" costs nothing and cannot rot.
# scripts/analytics/ is Backend's path and is not touched here.

GATEWAY_SCRIPTS := scripts/analytics

# These three moved from `docker run` to compose on 2026-09-01. The scripts in
# scripts/analytics/ still work and are still the way to run an ad-hoc variant
# (a second CDC loader under a different --name, say), but the managed path is
# compose: `docker run --rm` made `docker stop` DELETE the container, and left
# all three invisible to `docker compose ps`, `make down` and `make stats`.

.PHONY: up-platform
up-platform: ## Start the shared platform stack (login-gateway; keys generated on first run)
	@# gen-jwt-keys.sh REFUSES to overwrite an existing key (--force to replace),
	@# so this is safe on every run and removes the one manual step that stood
	@# between a fresh clone and a working gateway. Rotating keys behind live
	@# tokens is the failure it protects against; it is not this target's job.
	@bash $(GATEWAY_SCRIPTS)/gen-jwt-keys.sh
	@$(DC_PLATFORM) up -d --build login-gateway
	@echo "login-gateway  http://127.0.0.1:$${LOGIN_GATEWAY_HOST_PORT:-38120}   jwks: /.well-known/jwks.json"

.PHONY: up-gateway
up-gateway: up-platform ## Alias of up-platform, kept because the runbooks name it

.PHONY: up-semantic
up-semantic: ## Start the semantic API (run up-platform FIRST - it verifies against the gateway's JWKS)
	@$(DC_INSIGHT) up -d --build semantic-api
	@echo "semantic-api   http://127.0.0.1:$${SEMANTIC_API_HOST_PORT:-38200}"

.PHONY: up-portal
up-portal: ## Start the Insight portal (needs semantic-api and login-gateway up)
	@$(DC_INSIGHT) up -d --build insight-portal
	@echo "insight-portal http://127.0.0.1:$${INSIGHT_PORTAL_HOST_PORT:-33000}"

.PHONY: portal-build
portal-build: ## Rebuild the Insight portal image without starting it
	@$(DC_INSIGHT) build insight-portal

.PHONY: cdc-start
cdc-start: ## Provision the publication, then start the CDC loader (TENANT=<slug>)
	@# Publication FIRST, then the consumer, which creates the slot at the end of
	@# its own startup checks. Never the reverse - see the ordering note above.
	@bash $(GATEWAY_SCRIPTS)/cdc-provision.sh --slug $(if $(TENANT),$(TENANT),$${ODOO_DB_NAME:-bct})
	@$(DC_INSIGHT) up -d --build cdc

.PHONY: cdc-status
cdc-status: ## Show the CDC loader, its replication slot and its last success
	@# Reports; never fails. A status command that exits non-zero because the
	@# thing is down is a status command people stop running.
	@#
	@# "NOT running" here can mean DELETED, not stopped. cdc-run.sh uses
	@# `docker run --rm`, so `docker stop odoo19-bct-cdc` removes the container
	@# outright and a later `docker start` fails with "no such container" while
	@# the loader stays down. Do not reach for `docker start`; `make cdc-start`
	@# is the remedy in both cases, because cdc-run.sh does `docker rm -f` and
	@# recreates. Found by Frontend, the hard way.
	@echo "container:"
	@docker ps --format '  {{.Names}}	{{.Status}}' --filter name=odoo19-bct-cdc 2>/dev/null | grep . || echo "  odoo19-bct-cdc is NOT running  (make cdc-start)"
	@echo "replication slot:"
	@$(DC) exec -T postgres psql -U $${POSTGRES_USER:-odoo} -d $${ODOO_DB_NAME:-bct} -tAc 	    "SELECT '  ' || slot_name || '  active=' || active || '  wal_status=' || COALESCE(wal_status,'?') || '  retained=' || pg_size_pretty(COALESCE(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn), 0)) FROM pg_replication_slots" 2>/dev/null | grep . || echo "  no replication slot exists  (make cdc-start)"
	@echo "note: bct_cdc_up and slot-lag alerting are covered by 'make check-alerting'"

# ===========================================================================
# RESERVED — do not define these here.
#
# Namespace claimed by later agents (docs/agents/contracts/04-platform.md):
#   Data Warehouse : CLAIMED above, in the "Analytics warehouse" section.
#   Backend        : DEFINED above, in "Backend service tier". All four.
#   Frontend       : up-portal  portal-build — DEFINED above as of 2026-09-01,
#                    in "Backend service tier", when the portal was folded into
#                    compose/insight.yml. They are no longer reserved.
#
# Claimed since publication, on request via the Lead:
#   QA             : test  test-coldstart          (recipes here, tests/run.sh is QA's)
#   Backend        : metric-fixtures               (recipe here, the script is Backend's)
#   Security       : lint  sast  sbom  sign  ci-local
#
# Adding a target with one of those names silently overrides theirs, because
# make takes the LAST definition. Check this list before naming a new target.
# ===========================================================================

.PHONY: scan-local
scan-local: ## Run CI's static scanners locally via digest-pinned Docker images (S-2). ARGS=semgrep|hadolint|sqlfluff|gitleaks|trivy|all
	@bash scripts/scan-local.sh $(or $(ARGS),all)
