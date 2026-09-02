---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_pdp_audit
manifest_version: 19.0.0.3.0
---

# custom_pdp_audit

## Purpose
Provides the **append-only, hash-chained audit log** required by UU 27/2022 plus a reusable mixin (`pdp.audited.mixin`) that any model can inherit to push `create`/`write`/`unlink` events into it. The audit log itself lives outside the standard Odoo schema in a dedicated `pdp` PostgreSQL schema (table `pdp.audit_log`, sha256-chained, with a `pdp.verify_audit_chain()` function and a `pdp.audit_log_v` read view). A `pre_init_hook` bootstraps the schema on install so that fresh Odoo databases work without external init scripts.

The Odoo-side model `pdp.audit.log` (note the dot) is a read-only `_auto=False` view over `pdp.audit_log_v`; writes go straight through raw SQL `INSERT` from the mixin (and from other modules like `custom_coretax`, `custom_pdp_masking`).

## Business Flow
- On module install, `pre_init_hook` (Odoo 19 receives `env`) creates extensions (`unaccent`, `pg_trgm`, `pgcrypto`, `btree_gin`) and executes `data/02-pdp-schema.sql` (shipped under the addon's `data/`). If absent, install proceeds with a warning and the `pdp` schema is not created — every subsequent audit write will silently fail (logged as ERROR).
- `pdp.audited.mixin` overrides `create()`, `write()`, `unlink()` to call `_pdp_audit_write(action, res_id, sanitized_vals)`. `_sanitize_vals` truncates strings > 512 chars and replaces binaries with `<binary:Nb>`.
- `_pdp_audit_write` inserts via raw SQL into `pdp.audit_log` with actor_user_id, actor_login, tenant_db (`cr.dbname`), model_name, res_id, action, field_changes JSONB, classification (computed), ip_address, user_agent, request_id, reason. PostgreSQL trigger on the table computes `prev_hash_hex`/`hash_hex` (sha256 chain) so tampering is detectable. The INSERT is wrapped in `cr.savepoint(flush=False)` (since 19.0.0.1.1) so a DB-level failure rolls back only the audit statement — never the calling business transaction.
- `_pdp_audit_classification()` returns the highest-priority classification code among the model's PDP-tagged fields, ordered `sensitive_pii > health > financial > pii > confidential > internal > public`.
- `res.partner` and `res.users` are explicitly inherited to `pdp.audited.mixin` so all PII-bearing core records emit audit rows out of the box.
- The Odoo read-only view `pdp.audit.log.init()` rebuilds the SQL view in `tools.drop_view_if_exists` + `CREATE OR REPLACE VIEW`.
- `action_verify_chain` calls `pdp.verify_audit_chain(NULL)` and shows a green or red `display_notification` listing the first 10 broken row ids.

## Key Models
- `pdp.audited.mixin` (AbstractModel) — Mixin that overrides ORM CRUD to emit audit rows. Inherit it on any model that holds PII or other classified data.
- `pdp.audit.log` (Model, `_auto=False`) — Read-only view over `pdp.audit_log_v`; primary UI for inspecting the chain.
- `res.partner` / `res.users` (inherited) — Pre-mixed with `pdp.audited.mixin`.

## Important Fields
- `pdp.audit.log.ts` (Datetime, readonly) — server timestamp set by the PG trigger.
- `pdp.audit.log.actor_user_id` (Integer) / `actor_login` (Char) — caller identity at write time.
- `pdp.audit.log.tenant_db` (Char) — `cr.dbname`; relevant in multi-tenant single-cluster deployments.
- `pdp.audit.log.model_name` (Char, indexed) / `res_id` (Integer) — the affected record.
- `pdp.audit.log.action` (`Char`, DB column `varchar(64)` with CHECK `action ~ '^[a-z][a-z0-9_]{1,63}$'`) — free-form lowercase snake_case action code. Kept open (not a closed enum) so modules add domain actions (`approval_submit`, `fsm_wo_complete`, `pph_withholding_applied`, …) without a migration.
- `pdp.audit.log.field_changes` (Json) — sanitized vals dict; binaries → `"<binary:Nb>"`, strings >512 → truncated with ellipsis.
- `pdp.audit.log.classification` (Char, indexed) — top-priority classification of the source record.
- `pdp.audit.log.ip_address` (Char) / `user_agent` (Text) / `request_id` (Char) — pulled from `request.httprequest.environ` when called inside an HTTP request.
- `pdp.audit.log.prev_hash_hex` / `hash_hex` (Char) — sha256 chain computed by PG trigger.

## Public Methods
- `pdp.audited.mixin._pdp_audit_write(action, res_id, field_changes, reason=None)` (`@api.model`) — best-effort raw-SQL INSERT; never raises. Used by all PDP modules and `custom_coretax`/`custom_pph_witholding` for non-CRUD events (consent_grant, dsar, xml_export, sertel_access, etc.).
- `pdp.audited.mixin._pdp_audit_classification()` — returns the priority-ordered classification code for `self._name`.
- `pdp.audit.log.action_verify_chain()` — runs the PG verify function, returns a `display_notification` action.
- `pdp.audit.log.init()` — rebuilds the SQL view (called on registry init).
- `pre_init_hook(env)` (in `hooks.py`) — boots the `pdp` schema from `data/02-pdp-schema.sql`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_taxonomy`.
- **Inherits from:** `res.partner`, `res.users` (adds the mixin).
- **Extended by:** `custom_pdp_consent`, `custom_pdp_dsar`, `custom_pdp_retention`, `custom_pdp_masking`, `custom_coretax`, `custom_coretax_bupot`, `custom_pph_witholding`, `custom_rental`, and many more — anywhere `pdp.audited.mixin` is mixed in.
- **External calls:** Postgres-only (raw SQL into `pdp.audit_log`); no network.
- **Cross-vertical:** generic infrastructure.

## Gotchas
- **The `pdp` schema must exist before any audited write fires.** Pre-init runs `data/02-pdp-schema.sql` shipped with the addon. If you delete or fail to ship it, audit writes will fail silently (only an ERROR log line). Verify install: `SELECT 1 FROM pdp.audit_log LIMIT 1;`.
- **Audit writes use `self.env.cr.execute` directly** — they participate in the calling transaction, but the INSERT is wrapped in a `cr.savepoint(flush=False)` so a *failed* audit write rolls back only itself (logged ERROR) and does NOT poison the request. A successful audit row still commits/rolls back with the business write (no orphan rows). **History:** before 19.0.0.1.1 there was no savepoint, so a failed INSERT left the whole txn aborted → the next ORM query died with `InFailedSqlTransaction` (e.g. `account.move` posting crashing inside an unrelated `search`).
- **Old DBs may carry a stale `pdp.audit_log` schema.** Because the bootstrap uses `CREATE TABLE IF NOT EXISTS`, tables created before the schema bump kept `action VARCHAR(16)` + a closed-enum CHECK; long/new action codes then failed the INSERT. All three SQL copies (`postgres/init/02-pdp-schema.sql`, addon `data/02-pdp-schema.sql`, addon `data/pdp_schema.sql`) now carry an idempotent `DO $pdp_mig$` block that widens `action` to `varchar(64)` and swaps the enum CHECK for the regex — no-op on fresh/current DBs. `PdpAuditLog.init()` runs `data/pdp_schema.sql` on every `-u`, so `-u custom_pdp_audit` self-heals an existing DB.
- **`_sanitize_vals` does NOT capture the pre-image** — only field-name keys + truncated repr. You cannot reconstruct "value before write" from the audit log. By design, to avoid PII duplication.
- **`action` is a free-form `Char` (regex-CHECK'd at the DB), not a `Selection`.** Any lowercase snake_case code up to 64 chars is accepted; a code that violates the regex (uppercase, spaces, >63 chars) fails the INSERT — now caught by the savepoint and logged rather than crashing the request.
- **`request` import is best-effort** — if you call `_pdp_audit_write` outside an HTTP context (cron, RPC), ip/ua/request_id are all NULL. Counted on purpose; no errors.
- **`res.partner` / `res.users` inheritance is hot** — every login, every contact write is now an audit row. Plan storage (the `pdp.audit_log` table grows fast) and a separate retention story (`custom_pdp_retention` doesn't auto-prune the audit table by design).
- **Hash chain verification (`pdp.verify_audit_chain`) walks the entire table** — on multi-million-row tables this is slow; consider scheduling off-hours.
- **This module's groups sit on its own `custom_pdp_audit.res_groups_privilege_pdp_audit` privilege** ("PDP Audit") — Odoo 19 renders every group sharing one `res.groups.privilege` as a single pick-one dropdown on the user form, so a privilege shared across modules makes saving a user silently drop the other modules' groups. The privilege carries `custom_core.module_category_custom_platform`, so the selector still appears alongside the other custom modules. Do not point new groups at `custom_core.res_groups_privilege_custom_platform`.

## Out of Scope
- **Retention/rotation of the audit log itself** — the chain is meant to be append-only forever; offloading to cold storage is operator policy.
- **Read auditing of business records** — only `create`/`write`/`unlink` are mixed in here. Read events come from `custom_pdp_masking` (`pii_mask` action) and explicit callers.
- **Cross-tenant chain aggregation** — each tenant DB has its own `pdp.audit_log`; no central ledger.
- **Login/logout events** — selection codes exist (`login`, `logout`) but no `res.users` override emits them here. Wire them in a downstream auth module if needed.
- **Tamper alerting / SIEM forwarding** — the chain detects tamper but no notification is sent; build an external SIEM if required.
