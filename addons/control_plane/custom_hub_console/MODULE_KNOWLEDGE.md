---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_hub_console
manifest_version: 19.0.0.2.0
---

# custom_hub_console

# custom_hub_console

## Purpose
Top-level **control-plane** wrapper for the platform: aggregates tenants, modules, deployments, audit, monitoring, and AI usage under one navigation tree, and provides the per-tenant Hub dashboard that ops/CSM teams use to drive day-to-day operations. Owns the **module catalog** (scanned from `addons/`), the **per-tenant deployment ledger** with canary/rollback orchestration, an **append-only hash-chained audit log**, and the **AI usage roll-up**. Lives on the platform master DB alongside `custom_super_admin`.

## Business Flow
- **Catalog scan**: `custom.hub.module.catalog._action_scan_all()` walks `addons/core|compliance|ee_gap|operations|verticals/<module>/__manifest__.py`, parses each manifest with `ast.literal_eval`, counts `_name=` / `_inherit=` via regex inside `models/*.py`, and upserts a `custom.hub.module.catalog` row with `module_name`, `version`, `category` (core/compliance/ee_gap/operations/vertical), `summary`, model counts, and `maturity` heuristic (≥5 own models + `tests/` → production; 0 → scaffold; else partial). Triggered by the rescan wizard or scan cron.
- **Deploy to tenant**: User clicks `action_open_deploy_wizard` on a catalog row → `custom.hub.deploy.module.wizard` → creates `custom.hub.module.deployment(catalog_id, tenant_id, deploy_mode={install,upgrade,uninstall})` and calls `action_deploy()`. The deploy method POSTs `POST /v1/tenants/<slug>/modules/<mode>` body `{module: <name>}` via `custom.super.admin.orchestrator.client._request` (HMAC-signed). On success: state → installed/uninstalled. On failure: state → failed + `error_message` (does NOT raise — wizard commits).
- **Canary path (Track C)**: `action_resolve_dependencies` → topo-sort of `catalog.depends_module_ids`, written as JSON to `dep_graph_resolved_json`. `action_take_pre_backup` → calls `orchestrator.run_backup(slug, kind="manual")`, syncs the backup ledger, links newest snapshot as `rollback_snapshot_id`. `action_deploy_canary` → POSTs with `phase=canary` and `environment=<staging env name>` (resolved via `_pick_canary_environment` from `tenant.environment`). `action_healthcheck` → reads latest `custom.ops.tenant.health` snapshot for the tenant; pass iff `status="green"` AND `snapshot_at >= now()-5min`. `action_rollout_full` → blocked unless `healthcheck_passed`; POSTs `phase=full`. `action_rollback` → calls `orchestrator.restore_backup(slug, snapshot.s3_key)`, sets `canary_phase=rolled_back` and `state=failed`.
- **Audit chain**: Every `_log_audit` call creates a `custom.hub.audit.event` row via `log()`. `create()` resolves `prev_hash` from latest existing row, computes `hash = sha256(canonical_json({timestamp, user_id, event_type, tenant_id, object_ref, summary, payload, prev_hash}))`. `write()` and `unlink()` ALWAYS raise `UserError` — truly append-only. `verify_chain()` re-walks and reports `bad_ids`. Genesis row seeded from `data/audit_event_seed.xml` with `prev_hash=""`.
- **AI usage roll-up**: `custom.hub.ai.usage._cron_refresh(lookback_days=7)` calls `Bridge._hub_usage_iter(since=cutoff)` IF the bridge implements that helper; buckets by `(tenant_id, date, model_name)`; upserts (unique constraint). `cache_hit_rate_pct` is computed from `cache_read_tokens / (input + cache_read + cache_creation)`.
- **Per-tenant Hub view**: `tenant.registry` (inherited) gets `business_domain`, `deployment_topology`, `vpn_endpoint`, `assigned_module_ids`, `assigned_capability_ids`, computed `health_status` (pulls from `custom.ops.tenant.health` if installed, else `unknown`), `last_incident_id`. All sibling-module access is guarded by `_hub_is_module_installed(name)`.
- **OWL dashboard**: `web.assets_backend` registers `hub_dashboard.js`/`.xml`/`.scss`; an `ir.actions.client` action `hub_dashboard_action` opens the heatmap + cards UI; menus wired up `post_init_hook="_post_install_link_menus"`.

## Key Models
- `custom.hub.module.catalog` — Catalog of every platform addon scanned from `addons/`. Unique by `module_name`. Carries capability tags + dep graph.
- `custom.hub.module.deployment` — One row per (module, tenant) operation. Inherits `mail.thread`, `mail.activity.mixin`. Holds canary state, rollback snapshot link, healthcheck result.
- `custom.hub.audit.event` — Append-only hash-chained audit log. `write`/`unlink` raise. `verify_chain()` validates the chain.
- `custom.hub.ai.usage` — Per-tenant per-day per-model AI usage aggregate; unique `(tenant_id, date, model_name)`.
- `tenant.registry` (inherited) — Adds business_domain, deployment_topology, VPN endpoint, assigned modules/capabilities, computed health.
- `custom.hub.deploy.module.wizard` — TransientModel; staging form before `create + action_deploy`.
- `custom.hub.rescan.catalog.wizard` — TransientModel; triggers `_action_scan_all`.

## Important Fields
- `custom.hub.module.catalog.module_name` (Char, unique, indexed) — `__manifest__.py` directory name.
- `custom.hub.module.catalog.category` (Selection core/compliance/ee_gap/operations/vertical, indexed) — derived from `addons/<bucket>/` location.
- `custom.hub.module.catalog.maturity` (Selection scaffold/partial/production, indexed) — heuristic from model count + tests.
- `custom.hub.module.catalog.capability_tag_ids` (M2m custom.module.capability.tag) — BRD-analyzer tag mapping.
- `custom.hub.module.catalog.depends_module_ids` (M2m self) — dep graph used by `action_resolve_dependencies`.
- `custom.hub.module.catalog.models_own_count` / `models_inherit_count` — `_name=` and `_inherit=` regex matches.
- `custom.hub.module.deployment.deploy_mode` (Selection install/upgrade/uninstall, indexed) — operation type.
- `custom.hub.module.deployment.state` (Selection pending/installing/installed/upgrading/failed/uninstalled, indexed, tracking) — lifecycle.
- `custom.hub.module.deployment.canary_phase` (Selection none/canary/staged/full/rolled_back, indexed, tracking) — Track C phase.
- `custom.hub.module.deployment.rollback_snapshot_id` (M2o tenant.backup, set_null) — pre-deploy snapshot.
- `custom.hub.module.deployment.healthcheck_passed` (Boolean) / `healthcheck_at` (Datetime) — canary gate.
- `custom.hub.module.deployment.dep_graph_resolved_json` (Text, JSON) — `{"order": [...], "missing": [...]}`.
- `custom.hub.module.deployment.environment_id` (M2o tenant.environment, injected by `custom_tenant_infra`) — optional target environment.
- `custom.hub.audit.event.event_type` (Selection vertical_provision/vertical_suspend/module_deploy/module_upgrade/brd_approve/incident_acknowledge/ai_config_change/secret_rotate/genesis, indexed) — taxonomy.
- `custom.hub.audit.event.prev_hash` / `hash` (Char) — SHA-256 hex chain.
- `custom.hub.audit.event.object_ref` (Reference, dynamic whitelist `_selection_object_ref`: tenant.registry / catalog / deployment / res.users) — related object.
- `custom.hub.audit.event.payload` (Json) — event-specific data; part of hash.
- `custom.hub.ai.usage.cache_hit_rate_pct` (Float, computed, stored) — `cache_read / (input + cache_read + cache_creation) * 100`.
- `tenant.registry.business_domain` (Selection rental/manufacturing/retail/services/government/finance/healthcare/logistics/ppob/wms/other, indexed, tracking).
- `tenant.registry.health_status` (Selection green/yellow/red/unknown, computed, store=False) — passthrough to `custom.ops.tenant.health.status` latest.

## Public Methods
- `custom.hub.module.catalog._action_scan_all()` — addons walk + upsert. Idempotent, never deletes.
- `custom.hub.module.catalog._addons_root()` / `_parse_manifest(path)` / `_count_models(module_path)` — scan internals.
- `custom.hub.module.catalog.action_open_deploy_wizard()` — opens deploy wizard.
- `custom.hub.module.deployment.action_deploy()` — synchronous orchestrator call; non-raising.
- `custom.hub.module.deployment.action_resolve_dependencies()` / `action_take_pre_backup()` / `action_deploy_canary()` / `action_healthcheck()` / `action_rollout_full()` / `action_rollback()` — Track C canary flow.
- `custom.hub.module.deployment._pick_canary_environment()` — defensive lookup of staging `tenant.environment`.
- `custom.hub.module.deployment._log_audit(rec, event_type, success, error, extra)` (`@api.model`) — convenience wrapper around audit `log()`.
- `custom.hub.audit.event.log(event_type, summary, payload=None, tenant_id=False, object_ref=False, user_id=False)` (`@api.model`) — append helper.
- `custom.hub.audit.event.verify_chain()` (`@api.model`) — re-walks chain, returns `{checked, bad_ids, ok}`.
- `custom.hub.ai.usage._cron_refresh(lookback_days=7)` (`@api.model`) — pull + bucket usage.
- `tenant.registry._hub_is_module_installed(name)` (`@api.model`) — sibling-presence check.

## Integration Points
- **Depends on:** `custom_core`, `custom_super_admin`, `custom_ai_features`, `custom_brd_analyzer`, `custom_ops_monitor`, `mail`.
- **Inherits from:** `tenant.registry` (vertical extension); `mail.thread`+`mail.activity.mixin` on deployment.
- **Extended by:** `custom_tenant_infra` (injects `environment_id` on deployment + canary wizard).
- **External calls:** indirect — via `custom.super.admin.orchestrator.client._request` (HMAC-signed orchestrator API).
- **Cross-vertical:** platform control plane; not a customer-facing vertical.

## Gotchas
- **Catalog scan reads filesystem from `_addons_root()`** which derives the path from `__file__` location (`models/../../..`). If the module is installed from a non-standard layout, the scan finds nothing.
- **Manifest parse via `ast.literal_eval`** — manifests with `# -*- coding -*-` headers parse fine but anything beyond a literal dict (e.g. function calls in version string) silently returns None and is skipped.
- **`models_own_count` is regex-based**, not AST-based — multi-line `_name` declarations or `_name="..."` inside docstrings inflate the count.
- **Audit `write` / `unlink` always raise** — even installing data via XML cannot mutate existing rows. Genesis row uses `noupdate=1` to avoid re-creation.
- **`prev_hash` is computed at create() time** by reading the latest existing row — concurrent creates from different workers can produce **chain divergence**. There's no DB-level serialisation. In high-throughput audit scenarios this matters.
- **`canonical_payload` includes `object_ref` as `f"{_name},{id}"` when reading back** but as the raw value (a string from form, or a recordset) when writing — `verify_chain` and `create` must agree on the serialisation; the code uses `vals.get("object_ref")` raw on create and a reconstructed string on verify. Subtle: if you create with a recordset object, the original hash and the verify-time hash will differ.
- **`action_deploy` swallows orchestrator errors** (sets state=failed) so the wizard commits the deployment row. Good UX but hides crashes — check `error_message` not just exceptions.
- **`_SEED_PACK_MODULES` may name modules that do not exist yet.** `_link_seed_modules` resolves names with `Catalog.search([("module_name", "in", names)])`, so unknown names are silently skipped — listing a module ahead of its code landing is safe and simply links nothing until the catalog scan finds it.
- **`_link_seed_modules` only fills packs whose `module_ids` is EMPTY.** Adding a name to `_SEED_PACK_MODULES` therefore has **no effect on any database where that pack was already linked** — operator edits are deliberately preserved. To pick up a newly added module on an existing tenant, clear the pack's `module_ids` (so the next catalog scan refills it) or add the module to the pack by hand. Do not expect an upgrade to propagate the change.
- **`_pick_canary_environment` returns False when `tenant.environment` model is absent** — canary deploys then send no `environment` key; orchestrator must default sensibly.
- **`_cron_refresh` is no-op unless `custom_ai` exposes `_hub_usage_iter`** (the bridge does NOT in the current codebase) — AI usage aggregate stays empty until that helper is added.
- **`tenant.registry.health_status` is `store=False`** — cannot be used in search domains, only displayed.
- **`environment_id` field is declared in `custom_tenant_infra/models/hub_module_deployment_extension.py`**, not here. If `custom_tenant_infra` is uninstalled, that field disappears mid-flight from canary actions.

## Out of Scope
- **Actually installing the Odoo module on the tenant** — this module records intent and tells the orchestrator; the orchestrator's pgconnect/install is the real work.
- **Tenant provisioning** — owned by `custom_super_admin`.
- **VPS lifecycle** — owned by `custom_tenant_infra`.
- **BRD analysis / generation** — owned by `custom_brd_analyzer`.
- **Real-time CI/CD pipeline integration** — `custom_dev_cycle` handles webhooks; hub_console only reads deployment outcomes.
- **Multi-cluster federation** — single orchestrator endpoint.
