---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_pdp_retention
manifest_version: 19.0.0.2.0
---

# custom_pdp_retention

## Purpose
Implements **data retention policies** for UU 27/2022. Operators define one `pdp.retention.policy` per `(model, classification)` pair specifying a retention window in days, a date field to age against, and one of three actions: `anonymize`, `archive`, or `delete`. A daily cron iterates active policies and applies the action to eligible rows in bounded batches, writing a `custom` audit row per execution.

## Business Flow
- DPO creates `pdp.retention.policy(model_id, classification_id, retention_days, action, date_field="create_date")`. Constraint `_policy_unique` blocks duplicates per `(model_id, classification_id)`.
- `_compute_display_name` produces `"<model>/<classification.code>"`; `_compute_next_run` projects `last_run + 1 day`.
- `_compute_eligible` (non-stored) calls `_count_eligible()` → `Model.sudo().search_count(_eligible_domain())` where `_eligible_domain` is `[(date_field, "<", now - retention_days)]`.
- Cron `cron_apply_retention(limit_per_policy=500)` (defined in `data/pdp_retention_cron.xml`) iterates active policies and calls `_apply(limit=500)` per policy with per-policy try/except.
- `_apply` searches eligible rows (limit=500) and:
  - `delete` → `recs.unlink()`; on failure, `affected=0`, warning logged.
  - `archive` → `recs.write({"active": False})` only if the model has an `active` field; otherwise skipped.
  - `anonymize` → `_anonymize_records(recs)` overwrites only the fields whose `x_pdp_classification_id == self.classification_id` with `ANON-<sha256-prefix>` (char/text/html) or `False` (binary). Per-record failures are swallowed; returns count of successfully-touched records.
- `last_run` is stamped, and if `affected>0` a `pdp.audit_log` row with action `custom` is appended (via `pdp.audited.mixin._pdp_audit_write`) describing policy/model/action/count.
- Manual button `action_run_now` raises the limit to 2000 and displays a notification.
- Seed defaults loaded from `data/pdp_retention_defaults.xml`.

## Key Models
- `pdp.retention.policy` — Per-(model, classification) retention rule; inherits `pdp.audited.mixin`.

## Important Fields
- `pdp.retention.policy.model_id` (M2o `ir.model`, required, `ondelete="cascade"`) — target model.
- `pdp.retention.policy.model_name` (Char, `related="model_id.model"`, stored, indexed) — denormalised name.
- `pdp.retention.policy.classification_id` (M2o `pdp.classification`, required, `ondelete="restrict"`) — only fields with this classification are anonymized; ignored for `delete`/`archive`.
- `pdp.retention.policy.retention_days` (Integer, required, default 1825 ≈ 5 years) — age cutoff.
- `pdp.retention.policy.action` (Selection: anonymize/archive/delete, default `anonymize`) — what to do.
- `pdp.retention.policy.date_field` (Char, default `create_date`) — field used in `_eligible_domain`; no validation that this field exists or is a Date(time).
- `pdp.retention.policy.last_run` (Datetime, readonly) — stamped after each `_apply`.
- `pdp.retention.policy.next_run` (Datetime, computed/stored from `last_run`) — informational projection (`last_run + 1d`).
- `pdp.retention.policy.records_eligible_count` (Integer, non-stored) — live count via `_count_eligible()`; may be expensive on big tables.
- `pdp.retention.policy.active` (Boolean, default True) — cron only processes active rows.

## Public Methods
- `pdp.retention.policy.cron_apply_retention(limit_per_policy=500)` (`@api.model`) — cron entry point; iterates active policies with per-policy try/except.
- `pdp.retention.policy.action_run_now()` — manual one-shot at `limit=2000`; returns `display_notification` action.
- `pdp.retention.policy._apply(limit=500)` — applies the policy to one batch.
- `pdp.retention.policy._anonymize_records(recs)` — returns count of records actually written; only touches fields tagged with `self.classification_id`.
- `pdp.retention.policy._eligible_domain()` / `_count_eligible()` — helpers for previews and cron.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_taxonomy`, `custom_pdp_audit`.
- **Inherits from:** `pdp.audited.mixin`.
- **Extended by:** none declared.
- **External calls:** none.
- **Cross-vertical:** generic.

## Gotchas
- **`date_field` is unchecked free-text** — typos like `"creat_date"` or pointing at a non-stored compute field will raise inside `_count_eligible`/`_apply` (caught and logged, but the policy never fires).
- **Cron batch is 500 records per policy per run** — on a fresh install with years of backlog you must either bump `limit_per_policy`, run `action_run_now` repeatedly, or wait many cron cycles.
- **`anonymize` only operates on fields whose classification matches the policy's classification_id** — so a policy on `(res.partner, pii)` will not touch `vat` (which is tagged `financial`). To anonymize a model fully you may need multiple policies.
- **`archive` silently no-ops** on models without an `active` field, but reports `affected=0` — no warning surfaced to the operator.
- **`delete` failures are swallowed** (foreign-key constraints, model rules) and just logged; the policy will re-attempt next cron run.
- **No tombstone**: deleted rows are gone; anonymized rows are not flagged. Re-running anonymization on already-anonymized rows is a no-op but wastes work.
- **`_compute_eligible` is unstored** but the view will execute it on every list refresh → on a 10M-row table this is a full table scan per policy per render. Consider hiding the column on production lists.
- **This module's groups sit on its own `custom_pdp_retention.res_groups_privilege_pdp_retention` privilege** ("PDP Retention") — Odoo 19 renders every group sharing one `res.groups.privilege` as a single pick-one dropdown on the user form, so a privilege shared across modules makes saving a user silently drop the other modules' groups. The privilege carries `custom_core.module_category_custom_platform`, so the selector still appears alongside the other custom modules. Do not point new groups at `custom_core.res_groups_privilege_custom_platform`.

## Out of Scope
- **Policy preview / dry-run UI** — count is shown but there is no "show me which 47 rows" button.
- **Backup-before-delete** — if you need a snapshot, build it separately.
- **Time-based archive (move to cold storage)** — `archive` is `active=False` only; no data movement.
- **Per-record exceptions / legal hold** — if a record must be retained (active investigation), there is no flag to exclude it from a matching policy.
- **Tenant-scoped policy templates** — policies live in the tenant DB; the multi-tenant orchestrator must seed them itself.
