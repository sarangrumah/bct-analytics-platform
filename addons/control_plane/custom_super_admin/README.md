# Custom Super Admin (Platform Operations)

Ops-only vertical that runs in the **master_admin** Odoo database (reached
at `https://admin.platform.localhost`). Provides UI for the multi-tenant
control plane: provision, suspend, archive, backup, restore — all driven
through the `tenant-orchestrator` REST API.

## Models

- `tenant.registry` — local mirror of `tenant_registry.tenants` (master DB).
  Synced every minute by cron. Never written from the UI directly; the
  action buttons issue HMAC-signed orchestrator calls.
- `tenant.action.log` — append-only mirror of `tenant_registry.action_log_v`.
  Synced every 2 min. Includes a "Verify Chain Integrity" action that calls
  the master-side `verify_action_chain()` function.
- `tenant.backup` — mirror of `tenant_registry.backups`, synced every 5 min
  per active tenant.
- `custom.super.admin.orchestrator.client` — abstract model wrapping the
  HMAC-signed HTTP client to `tenant-orchestrator`.
- `tenant.provision.wizard`, `tenant.restore.wizard` — transient models
  driving the provision + restore flows.

## Security Groups

- `custom_super_admin.group_csm` — Customer Success Manager. Read-only on
  tenants + backups + action log; can trigger on-demand backups.
- `custom_super_admin.group_super_admin` — full lifecycle (provision,
  suspend, archive, restore). Inherits CSM rights.

## Dependencies

- `custom_core` (HMAC signer + `custom.ir.config`)
- `mail` (notifications)
- Python: `httpx` (orchestrator client)

## Install

Installed only in the `master_admin` tenant database (not in regular
tenants). Provision via:

```bash
# Manually bootstrap the master_admin DB once (separate from regular tenant DBs):
docker compose exec odoo odoo -d master_admin -i custom_super_admin --stop-after-init
```

Then access `https://admin.platform.localhost`, log in as the master admin,
and start provisioning tenants under **Super Admin → Provision New**.

## Settings

Configure under Settings → Super Admin:

- **Orchestrator URL** — defaults to `http://tenant-orchestrator:8080`
  (internal Docker DNS).
- **Grafana Base URL** — used to build per-tenant dashboard links.

## How action buttons work

1. UI button (e.g. "Suspend") triggers a Python method on `tenant.registry`.
2. The method calls `self.env['custom.super.admin.orchestrator.client'].suspend(slug)`.
3. That client signs the payload with `ORCHESTRATOR_SHARED_SECRET` and POSTs
   to `tenant-orchestrator:8080/v1/tenants/<slug>/suspend`.
4. Orchestrator validates HMAC, executes the operation against master DB +
   Postgres + Odoo create-DB endpoint as needed, and logs to
   `tenant_registry.action_log` (append-only, hash-chained).
5. The action button triggers an immediate sync to refresh the local
   `tenant.registry` mirror.

## Audit trail

Two layers of audit:

- **Master DB** — `tenant_registry.action_log` is the source of truth.
  Append-only at the Postgres level (trigger blocks UPDATE/DELETE/TRUNCATE).
  Hash-chained — every row's `hash` includes the previous row's hash.
- **Odoo mirror** — `tenant.action.log` for UI consumption.
  "Verify Chain Integrity" action validates the master-side chain via
  `tenant_registry.verify_action_chain()` and surfaces a notification.

## Reference

- `tenant-orchestrator/README.md`
- `postgres/init/04-tenant-registry-schema.sql`
- `docs/sops/tenant-onboarding.md`
