---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_tenant_infra
manifest_version: 19.0.0.1.0
---

# custom_tenant_infra

## Purpose
Manages the per-tenant **VPS fleet** end-to-end from Odoo. Lets ops register a VPS, harden it, install Docker/Caddy, deploy the Odoo stack for one or more `tenant.environment` rows (dev/staging/prod), sync addons, run healthchecks, and decommission — all by clicking buttons that delegate to the HMAC-signed orchestrator API. Adds versioned jinja2 bootstrap-script templates stored as `ir.attachment` so ops can edit hardening scripts without a code deploy. Adds `environment_id` and `target_environment_id` fields onto hub_console's deployment model + canary wizard (because `custom_hub_console` cannot depend on `custom_tenant_infra` — dependency goes the other way).

## Business Flow
- **Register a VPS**: Ops creates `tenant.vps` (name, hostname unique, public_ip, ssh_port=22, ssh_user=root, `ssh_credential_ref` like `vault://prod/vps/{id}/ssh_key`, provider, region, hardware specs). State starts `registered`.
- **Link environments**: Create `tenant.environment` rows (env_type dev/staging/prod, `tenant_registry_id`, `db_name`, `vps_id`). SQL constraint `EXCLUDE (vps_id WITH =) WHERE (env_type = 'prod')` ensures one prod env per VPS; `unique(tenant_registry_id, env_type)` ensures one env per type per tenant.
- **Bootstrap**: `action_bootstrap()` → `_set_state("hardening")` → `deployer.bootstrap(vps)` → POST `/v1/vps/{id}/bootstrap` (jinja2-rendered hardening + Docker + Caddy script). On success `_set_state("bootstrapping")` then `"active"`. Failure raises `UserError` with the orchestrator error.
- **Deploy stack**: `action_deploy_odoo_stack()` requires state ∈ {active, degraded}. For each linked environment, calls `deployer.deploy_stack(vps, env)` → POST `/v1/vps/{id}/deploy-stack` with `env_type`, `tenant_slug`, `db_name`. Appends progress to `bootstrap_log` (OWL console streams via SSE).
- **Sync addons**: `action_sync_addons()` → for each env, `deployer.sync_addons(vps, env)` → POST `/v1/vps/{id}/sync-addons` (git pull + restart on the VPS).
- **Healthcheck**: `action_healthcheck()` → `deployer.healthcheck(vps)`, expects `{ok: bool}`. On `ok=False` while active → state transitions to `degraded`; on `ok=True` while degraded → back to `active`. Stamps `last_health_check_at`.
- **Decommission**: `action_decommission()` → orchestrator call → state `decommissioned` (terminal).
- **Bootstrap templates**: `tenant.vps.bootstrap.template` stores versioned jinja2 scripts per `script_kind` (harden_os/install_docker/install_caddy/deploy_odoo) as `ir.attachment`. The orchestrator renders them with VPS-specific variables before scp'ing.
- **Hub deploy integration**: `custom.hub.module.deployment.environment_id` (injected here) lets canary deploys target a specific environment.

## Key Models
- `tenant.vps` — VPS inventory + state machine. Inherits `mail.thread`, `mail.activity.mixin`. Unique by hostname.
- `tenant.environment` — Per-environment deployment record. Composite uniqueness; prod is 1:1 with a VPS via SQL EXCLUDE constraint.
- `tenant.vps.bootstrap.template` — Versioned jinja2 shell script templates as `ir.attachment` wrappers. Unique `(script_kind, version)`.
- `tenant.vps.deployer` — AbstractModel; thin wrapper around `custom.super.admin.orchestrator.client._request`.
- `tenant.registry` (inherited via `tenant_registry_extension.py`) — adds `environment_ids` One2many + computed `primary_vps_id` (the VPS hosting the prod environment).
- `tenant.health.extension` (in `tenant_health_extension.py`) — minor extension hook on `custom.ops.tenant.health` to associate it with a VPS.
- `custom.hub.module.deployment` (inherited via `hub_module_deployment_extension.py`) — adds `environment_id` (M2o `tenant.environment`).
- `custom.hub.deploy.module.wizard` (inherited) — adds `target_environment_id`.

## Important Fields
- `tenant.vps.state` (Selection registered/hardening/bootstrapping/active/degraded/decommissioned, indexed, tracking) — validated via `ALLOWED_TRANSITIONS` in `_assert_transition`.
- `tenant.vps.hostname` (Char, required, tracking, unique constraint) — DNS-resolvable hostname.
- `tenant.vps.public_ip` (Char, tracking) — IPv4/IPv6.
- `tenant.vps.ssh_user` (Char, default `root`, required) / `ssh_port` (Integer, default 22, required).
- `tenant.vps.ssh_credential_ref` (Char, required) — vault pointer (`vault://...` or `env://VPS_SSH_KEY_PATH`). NEVER raw key material.
- `tenant.vps.provider` (Selection biznet/idcloudhost/digitalocean/hetzner/aws/other) — hosting provider.
- `tenant.vps.cpu_cores` / `ram_mb` / `disk_gb` (Integer) — capacity.
- `tenant.vps.os_version` / `docker_version` (Char) — detected by SSH facter during bootstrap.
- `tenant.vps.prometheus_target_url` / `grafana_dashboard_uid` (Char) — monitoring wiring.
- `tenant.vps.bootstrap_log` (Text) — append-only log stream `[<iso_ts>] <line>\n`, written by `_append_log`. Streamed to OWL via SSE.
- `tenant.vps.last_health_check_at` (Datetime) — last `action_healthcheck` timestamp.
- `tenant.vps.environment_ids` (One2many tenant.environment) + computed `environment_count`.
- `tenant.environment.env_type` (Selection dev/staging/prod, required, default dev) — environment class.
- `tenant.environment.db_name` (Char, required, validated non-blank by `_check_db_name`) — postgres DB.
- `tenant.environment.odoo_url` (Char) — public URL (set by orchestrator after deploy).
- `tenant.environment.addons_revision` (Char) — git SHA currently deployed.
- `tenant.environment.last_deploy_id` (Char) — orchestrator run id of last deploy.
- `tenant.environment.last_deploy_at` (Datetime).
- `tenant.environment.name` (Char, computed `<slug>/<env_type>`).
- `tenant.vps.bootstrap.template.script_kind` (Selection harden_os/install_docker/install_caddy/deploy_odoo, indexed, required) — script taxonomy.
- `tenant.vps.bootstrap.template.script_attachment_id` (M2o ir.attachment, restrict, required) — holds the jinja2 body.
- `tenant.vps.bootstrap.template.variables_json` (Json) — default jinja2 variables merged with per-VPS values at render time.

## Public Methods
- `tenant.vps.action_register_with_orchestrator()` — POST `/v1/vps/register`; logs.
- `tenant.vps.action_bootstrap()` — state transitions hardening→bootstrapping→active; orchestrator call.
- `tenant.vps.action_deploy_odoo_stack()` — per linked environment, POST `/v1/vps/{id}/deploy-stack`.
- `tenant.vps.action_sync_addons()` — per env, POST `/v1/vps/{id}/sync-addons`.
- `tenant.vps.action_healthcheck()` — POST and update `last_health_check_at`; bidirectional active↔degraded transition.
- `tenant.vps.action_decommission()` — terminal transition.
- `tenant.vps._set_state(new)` — validated state write.
- `tenant.vps._assert_transition(new)` — raises `UserError` on illegal moves per `ALLOWED_TRANSITIONS`.
- `tenant.vps._append_log(line)` — appends `[<ts>] <line>\n` to `bootstrap_log`.
- `tenant.vps.deployer.register(vps)` / `.bootstrap(vps)` / `.deploy_stack(vps, env)` / `.sync_addons(vps, env)` / `.healthcheck(vps)` / `.decommission(vps)` — orchestrator wrappers; reuses `custom.super.admin.orchestrator.client._request` for HMAC.
- `tenant.registry._compute_primary_vps()` — filters `environment_ids` where env_type=prod.

## Integration Points
- **Depends on:** `custom_super_admin`, `custom_ops_monitor`, `custom_hub_console`.
- **Inherits from:** `mail.thread`, `mail.activity.mixin` (on `tenant.vps`). Extends `tenant.registry`, `custom.hub.module.deployment`, `custom.hub.deploy.module.wizard`, `custom.ops.tenant.health`.
- **Extended by:** `custom_onboarding_journey` (`tenant_vps_id` + `tenant_environment_id` on journey).
- **External calls:** indirect — via `custom.super.admin.orchestrator.client._request` (HMAC-signed `${ORCHESTRATOR_URL}`).
- **Cross-vertical:** platform infrastructure; not customer-facing.

## Gotchas
- **SSH credentials NEVER stored in Odoo**: `ssh_credential_ref` is a pointer string; the orchestrator resolves it at SSH-time. If you literally put a private key in the field, the orchestrator will treat it as a vault URI and fail.
- **`bootstrap_log` grows unbounded** — `_append_log` does string concat; very long-lived VPS records accumulate megabytes. No truncation cron.
- **`ALLOWED_TRANSITIONS` is module-level dict** — you cannot programmatically override transitions; subclasses must re-implement.
- **`action_deploy_odoo_stack` filters environments by env_type ∈ {dev, staging, prod}** — but raises `UserError("No environments linked")` only if NONE exist; envs with other types are silently skipped (no other types are currently defined, so moot — but brittle).
- **`prod_unique_per_vps` uses PostgreSQL `EXCLUDE` constraint** with `WHERE (env_type = 'prod')` — requires PG ≥9.0 (fine) but the constraint syntax is Odoo's `_sql_constraints` raw SQL, which Odoo's auto-creator handles correctly only for simple types; verify on install.
- **`primary_vps_id` compute returns False when no prod env exists** — UI shows blank rather than warning.
- **`tenant.health.extension` is a soft integration** — `custom_ops_monitor` healthcheck cron does NOT automatically run `tenant.vps.action_healthcheck`; they're independent telemetry sources.
- **Demo data is loaded** (`data/demo_data.xml`) — `--without-demo=all` skips it in production.
- **Healthcheck bidirectionality** is opportunistic: only fires the transition when state is currently `active` or `degraded`. Healthchecks against a `decommissioned` VPS do not transition.
- **`environment_id` on `custom.hub.module.deployment` is declared here** — uninstalling `custom_tenant_infra` drops that field; existing canary deployments will lose their environment pointer.
- **No retention / archive of decommissioned VPS records** — they sit forever with `state=decommissioned`.

## Out of Scope
- **VPS provisioning at the cloud provider** — this module manages VPS records and the bootstrap of a pre-existing VPS. Spinning up a Hetzner/AWS instance is not here (an orchestrator extension would).
- **In-Odoo SSH execution** — all SSH is orchestrator-side.
- **Multi-region / cross-cluster orchestration** — single `${ORCHESTRATOR_URL}`.
- **Container runtime choice** — Docker is assumed; no Podman/k8s support.
- **TLS certificate management** — Caddy handles it transparently via Let's Encrypt; nothing to do here.
- **Disaster recovery procedures** — backup/restore is in `custom_super_admin` + orchestrator.
- **VPS cost tracking** — no billing fields.
