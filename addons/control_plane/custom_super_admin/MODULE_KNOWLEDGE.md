---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_super_admin
manifest_version: 19.0.0.2.0
---

# custom_super_admin

## Purpose
Ops-only **multi-tenant control plane** running in the platform's `master_admin` database. Provides the UI and HMAC-signed orchestrator client that lets ops and CSM provision, suspend, resume, archive, backup, and restore tenants without SSH. Mirrors the master DB's `tenant_registry.tenants`, `tenant_registry.backups`, and the append-only hash-chained `tenant_registry.action_log_v` view into Odoo models via cron — Odoo never writes to the source registry directly; writes go through the orchestrator REST API which then re-publishes to the registry. Plus a Grafana iframe link, retention-aware backup ledger, and croniter-aware scheduled-backup mechanism.

## Business Flow
- **Sync from orchestrator**: `_cron_sync_from_orchestrator` (every minute) calls `orchestrator_client.list_tenants()` → `_upsert_many` writes/updates `tenant.registry` rows; slugs missing from upstream are marked `archived` locally. `_cron_sync_for(slug)` per-tenant variant is called after each action button.
- **Provision a tenant**: User opens `tenant.provision.wizard` → orchestrator POST `/v1/tenants` with payload → wizard waits → sync cron picks up the new row.
- **Lifecycle actions** on a `tenant.registry` row:
  - `action_suspend` → `orchestrator_client.suspend(slug, reason)` → resync → notify.
  - `action_resume` → `orchestrator_client.resume(slug)`.
  - `action_archive` → `orchestrator_client.archive(slug, retention_days=30)` (sets `purge_after` in master DB).
  - `action_trigger_backup` → `orchestrator_client.run_backup(slug, kind="manual")` → calls `tenant.backup._cron_sync_for(slug)` to mirror the new backup row → success toast with `s3_key`/`size_bytes`.
  - `action_open_restore_wizard` → `tenant.restore.wizard` (pick a `tenant.backup`, optional target db) → orchestrator restore.
  - `action_open_replicate_wizard` → `tenant.replicate.wizard` (clone prod to staging-style env).
  - `action_open_grafana` → opens `<grafana_base_url>/d/tenant?var-db=<db_name>` in new tab.
- **Backup ledger**: `tenant.backup` mirrored from master DB via `_cron_sync_all` (uses orchestrator `list_backups(slug)`); per-row `_compute_size_human` formats bytes. Scheduled backups driven by `croniter` parsing `tenant.registry.backup_schedule` (default `"0 2 * * *"`).
- **Action log mirror**: `tenant.action.log._cron_sync` queries the master DB directly via `cr.execute("SELECT ... FROM tenant_registry.action_log_v WHERE id > %s ORDER BY id ASC LIMIT 5000")` — works ONLY because the runtime postgres user has been GRANTed `tenant_registry_reader` and the master and runtime DBs are in the same cluster. Skips silently if the `tenant_registry` schema isn't visible (e.g. running from a tenant DB instead of master_admin). `action_verify_chain()` calls master-side `tenant_registry.verify_action_chain()`.
- **All write methods on `tenant.registry`** are restricted to `custom_super_admin.group_super_admin`; the model is read-only otherwise from the UI.

## Key Models
- `tenant.registry` — Local mirror of master DB `tenant_registry.tenants`. Inherits `mail.thread`. Source of truth is master DB; UI writes go through orchestrator.
- `tenant.backup` — Mirror of master DB `tenant_registry.backups` ledger. Carries `s3_key`, `checksum_sha256`, `outcome`, `expires_at`.
- `tenant.action.log` — Append-only mirror of master DB `tenant_registry.action_log_v` (hash-chained). Direct SQL pull, schema-existence-guarded.
- `custom.super.admin.orchestrator.client` — AbstractModel; HMAC-signed httpx wrapper for `${ORCHESTRATOR_URL}/v1/...`.
- `tenant.provision.wizard` / `tenant.restore.wizard` / `tenant.replicate.wizard` — TransientModels; staging forms before orchestrator calls.

## Important Fields
- `tenant.registry.slug` (Char, required, indexed, copy=False, unique constraint) — DNS-safe tenant identifier.
- `tenant.registry.db_name` (Char, required) — postgres DB name for the tenant.
- `tenant.registry.state` (Selection provisioning/active/suspended/archived/failed, indexed, default provisioning) — lifecycle.
- `tenant.registry.activated_at` / `suspended_at` / `archived_at` / `purge_after` / `last_seen_at` (Datetime) — lifecycle stamps from orchestrator.
- `tenant.registry.last_backup_at` / `last_backup_size_bytes` / `last_backup_id` — latest backup pointer.
- `tenant.registry.csm_user_id` (M2o res.users) — assigned customer success manager.
- `tenant.registry.features` (Json) — orchestrator-managed feature flags.
- `tenant.registry.sync_error` (Text) — last orchestrator error per tenant.
- `tenant.registry.backup_schedule` (Char, default `"0 2 * * *"`) — 5-field cron expression (UTC) parsed by `croniter`.
- `tenant.registry.backup_retention_days` (Integer, default 30) — retention horizon.
- `tenant.registry.pitr_enabled` (Boolean, default False) — WAL-archiving toggle (set on master side).
- `tenant.registry.last_scheduled_backup_at` (Datetime, readonly) — last cron-driven backup timestamp.
- `tenant.backup.master_id` (Integer, indexed, required, unique constraint) — id in master DB.
- `tenant.backup.kind` (Selection manual/daily/monthly/yearly, required) — backup taxonomy.
- `tenant.backup.s3_key` / `checksum_sha256` (Char) — storage pointer + integrity.
- `tenant.backup.outcome` (Selection pending/success/failure, required) — result.
- `tenant.backup.size_human` (Char, computed) — pretty `n.n KB/MB/GB/TB/PB`.
- `tenant.backup.expires_at` (Datetime) — retention expiry.
- `tenant.action.log.master_id` (Integer, required, indexed, unique) — id in master DB action log.
- `tenant.action.log.detail` (Json), `outcome` (Selection success/failure/partial), `prev_hash_hex` / `hash_hex` (Char) — hash chain from master.

## Public Methods
- `tenant.registry._cron_sync_from_orchestrator()` / `_upsert_many(rows)` / `_to_dt(value)` (static) — sync internals.
- `tenant.registry.action_open_provision_wizard()` / `action_suspend()` / `action_resume()` / `action_archive()` / `action_trigger_backup()` / `action_open_restore_wizard()` / `action_open_replicate_wizard()` / `action_open_grafana()` / `action_view_backups()` / `action_view_action_log()` — UI buttons.
- `tenant.backup._cron_sync_all()` / `_cron_sync_for(slug)` — ledger sync.
- `tenant.action.log._cron_sync()` — direct-SQL pull from master DB.
- `tenant.action.log.action_verify_chain()` — calls master `tenant_registry.verify_action_chain()`.
- `custom.super.admin.orchestrator.client._request(method, path, *, body=None, actor=None)` — HMAC-signed httpx call; raises `RuntimeError` on ≥400.
- `custom.super.admin.orchestrator.client.list_tenants(state=None)` / `get_tenant(slug)` / `provision(payload)` / `suspend(slug, reason)` / `resume(slug)` / `archive(slug, retention_days=30)` / `run_backup(slug, kind)` / `restore_backup(slug, s3_key, target_db=None)` / `list_backups(slug)` — orchestrator REST API.

## Integration Points
- **Depends on:** `custom_core`, `mail`.
- **Inherits from:** `mail.thread` (on `tenant.registry`).
- **Extended by:** `custom_hub_console` (adds business_domain, deployment_topology, health_status, assigned modules), `custom_tenant_infra` (adds environments + primary_vps_id), `custom_ops_monitor` (cron reads `tenant.registry` for active tenants), `custom_onboarding_journey` (links journey to tenant), `custom_dev_cycle` (deployments per tenant).
- **External calls:** httpx POST/GET to `${ORCHESTRATOR_URL}` (default `http://tenant-orchestrator:8080`), signed via `custom.security.sign_for("ORCHESTRATOR_SHARED_SECRET", body)`. Direct SQL into master DB schema `tenant_registry` (same Postgres cluster only).
- **Cross-vertical:** platform control plane.

## Gotchas
- **Same-cluster requirement for `tenant.action.log`**: the direct SQL `SELECT ... FROM tenant_registry.action_log_v` works only if Odoo's Postgres user has been GRANTed `tenant_registry_reader` AND the master DB is in the same cluster (cross-DB query via `dblink` not used). On tenant DBs (where `tenant_registry` schema is absent) the cron silently no-ops.
- **`stale slugs are archived locally, not deleted**: `_upsert_many` writes `state="archived"` for slugs missing from upstream; this misclassifies temporarily-unreachable orchestrator responses.
- **`action_*` methods on `tenant.registry` raise `UserError` on orchestrator failure** — wizard does not commit, the user must retry.
- **`_request` raises `RuntimeError` on ≥400** — calling code does try/except and wraps in `UserError`; raw RuntimeError will leak to logs.
- **`DEFAULT_TIMEOUT = 180.0`** is hard-coded; long-running provision calls block the worker for up to 3 minutes.
- **HMAC secret must be in env** (`ORCHESTRATOR_SHARED_SECRET`); `custom.security` refuses `"changeme"` substrings.
- **`tenant.action.log.create` for mirror writes plain `create`**, not append-only-enforced (the master side is the source of truth chain; the mirror is just a cache). A privileged user can in principle write directly to the mirror without breaking the master chain.
- **Backup `expires_at` is informational only** — there's no purge cron locally; the master DB orchestrator is expected to delete S3 objects.
- **`backup_schedule` croniter is optional** — if `croniter` not installed (`external_dependencies.python`), the scheduled-backup mechanism is silently disabled.
- **`tenant.registry.write()` is allowed for the `group_super_admin` group** despite the mirror semantics — careful, edits won't propagate to master DB and will be overwritten on next sync.
- **Grafana URL hardcodes `var-db=<db_name>`** — Grafana dashboard variable name must match.

## Out of Scope
- **Tenant DB creation** — orchestrator owns this. Odoo only requests it.
- **Backup storage / S3 lifecycle** — orchestrator manages S3 keys; Odoo only mirrors metadata.
- **Tenant-internal user management** — out of scope; the super-admin doesn't manage end-user accounts inside tenant DBs.
- **PITR mechanics** — `pitr_enabled` is a flag that the orchestrator interprets.
- **Cross-cluster federation** — single orchestrator endpoint configured by `custom_super_admin.orchestrator_url`.
- **Module deployment** — owned by `custom_hub_console`.
- **VPS provisioning** — owned by `custom_tenant_infra`.
