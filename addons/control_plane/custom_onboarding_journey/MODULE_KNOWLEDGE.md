---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_onboarding_journey
manifest_version: 19.0.1.0.0
---

# custom_onboarding_journey

## Purpose
Single state machine that walks every prospective tenant from first intake to live tenant handover. Provides `onboarding.journey` with an explicit allowed-transitions graph, an append-only `onboarding.stage.transition` audit trail, a public-intake controller (`/onboarding/public/intake` + `/onboarding/public/status/<token>`) gated by Cloudflare Turnstile + per-IP rate-limiting, bi-directional sync with `project.project` (kanban columns ↔ stages with loop prevention and last-write-wins on `sync_version`), and a Go/No-Go wizard that creates an `approval.request`. Links the journey to BRD docs, the eventual `tenant.registry`, the `tenant.vps`, and the `tenant.environment`.

## Business Flow
- **Public intake**: Marketing site POSTs to `/onboarding/public/intake` with `{company_name, contact_email, ...optional brd_file_base64s, vertical_target, cf_turnstile_token, ...}`. Controller hashes source IP (SHA-256), enforces per-IP rate limit (process-local bucket, configurable `per_hour`), verifies Turnstile if `cf_turnstile_secret` configured, then `onboarding.public.submission.create_from_payload(payload)` writes a raw inbox row and returns `{token, status_url}`.
- **Promote to journey**: BA reviews submissions, clicks `action_promote_to_journey` → finds/creates `res.partner` by email → creates `onboarding.journey` with `stage=brd_uploaded` if `brd_file_base64s` present, else `intake`. For each uploaded BRD, decodes base64 (handles `data:...;base64,` prefix), creates `ir.attachment` then `brd.document(name, document_attachment_id, journey_id, ...)` and re-points the attachment.
- **Stage machine**: `_FORWARD` defines allowed transitions per stage: `draft → intake → brd_uploaded → brd_analyzed → recommendations_ready → go_no_go → provisioning_requested → provisioning_in_progress → tenant_live → handover → closed`. Any non-terminal stage can move to `rejected` or `on_hold`; `on_hold` can resume to any non-terminal stage. `write()` validates the transition (raises `ValidationError`), bumps `sync_version`, appends `onboarding.stage.transition`, posts chatter, auto-archives the linked `project.project` on `closed`. The append-only transition model's `write()` raises `AccessError` (only superuser may `unlink`).
- **Bi-directional project sync** (`journey_project_sync.py`): `create()` calls `_ensure_project` (creates `project.project` from template). On stage write, `_sync_stage_to_project_tasks(new_stage)` moves the "stage marker" task to the column from `STAGE_TO_COLUMN`. The reverse direction (task column change → journey stage update via `COLUMN_TO_STAGE`) is also wired. Loop prevention: both sides check `self.env.context.get("_skip_journey_sync")` and short-circuit. Conflicts resolved by `sync_version` last-write-wins.
- **Wizards**: `onboarding.intake.wizard` captures structured intake. `onboarding.brd.upload.wizard` uploads a BRD to the journey. `onboarding.go.no.go.wizard` creates an `approval.request` linked via `approval_request_id`; the journey advances to `provisioning_requested` on approval.
- **Public status endpoint** `/onboarding/public/status/<public_status_token>` exposes non-sensitive read-only stage + progress for the prospect.

## Key Models
- `onboarding.journey` — Central state machine. Inherits `mail.thread`, `mail.activity.mixin`. Links partner, BRDs, approval, tenant, VPS, environment, project.
- `onboarding.stage.transition` — Append-only audit row per stage move. `write()` raises `AccessError`.
- `onboarding.public.submission` — Raw inbox for public-site form submissions. Promoted to `onboarding.journey` by BA action.
- `brd.document` (extended via `brd_document_extension.py`) — adds `journey_id` back-reference.
- `brd.recommendation` (extended via `brd_recommendation_extension.py`) — adds `journey_id` derived link.

## Important Fields
- `onboarding.journey.stage` (Selection from `STAGE_SELECTION`, required, indexed, tracking) — drives the entire workflow.
- `onboarding.journey.partner_id` (M2o res.partner, restrict, tracking) — the prospect/customer.
- `onboarding.journey.brd_document_ids` (One2many brd.document) + `brd_recommendation_ids` (related, readonly) — uploaded analysis input + AI-generated recommendations.
- `onboarding.journey.approval_request_id` (M2o approval.request, set_null, copy=False) — Go/No-Go approval anchor.
- `onboarding.journey.tenant_registry_id` (M2o tenant.registry, set_null, copy=False) — materialized tenant.
- `onboarding.journey.tenant_vps_id` (M2o tenant.vps, set_null, copy=False) — provisioned VPS.
- `onboarding.journey.tenant_environment_id` (M2o tenant.environment, set_null, copy=False) — `prod` environment row.
- `onboarding.journey.project_id` (M2o project.project, set_null, copy=False, indexed) — synced kanban project.
- `onboarding.journey.project_orphaned` (Boolean, default False, copy=False) — set when project was archived/deleted but journey continues.
- `onboarding.journey.mandays_estimate` (Integer, computed, stored, depends `brd_recommendation_ids.estimated_md`) — sum of BRD recommendation effort.
- `onboarding.journey.target_go_live` (Date, tracking) — committed go-live date.
- `onboarding.journey.owner_id` / `ba_id` (M2o res.users, tracking) — owner + business analyst.
- `onboarding.journey.company_profile_json` (Text) — intake-captured JSON.
- `onboarding.journey.public_status_token` (Char, unique, indexed, default `secrets.token_urlsafe(24)`) — URL token for public status page.
- `onboarding.journey.sync_version` (Integer, default 0, copy=False) — last-write-wins counter for project sync.
- `onboarding.journey.progress_pct` (Integer, computed, stored, depends `stage`) — % of happy-path length.
- `onboarding.stage.transition.from_stage` / `to_stage` (Char) — transition delta. `write()` raises.
- `onboarding.public.submission.raw_payload_json` (Text, required) — verbatim incoming payload.
- `onboarding.public.submission.source_ip_hash` (Char, indexed) — SHA-256 hash of source IP (PDP-friendly, no raw IP).
- `onboarding.public.submission.status` (Selection submitted/promoted/rejected, required, indexed) — inbox lifecycle.
- `onboarding.public.submission.public_token` (Char, unique, indexed, `secrets.token_urlsafe(24)`) — anonymous tracking token.

## Public Methods
- `onboarding.journey.action_open_brds()` / `action_open_recommendations()` / `action_open_project()` / `action_open_tasks()` / `action_open_tenant()` / `action_open_vps()` — drill-down buttons.
- `onboarding.journey.action_launch_brd_upload()` / `action_launch_go_no_go()` — wizard launchers.
- `onboarding.journey._ensure_project()` — creates the per-journey `project.project` from template (called by `create()` unless `_skip_journey_sync`).
- `onboarding.journey._sync_stage_to_project_tasks(new_stage)` — moves the stage-marker task to `STAGE_TO_COLUMN[new_stage]`.
- `onboarding.journey._origin_stage_cache()` — approximates the previous stage via the latest transition row (Odoo 19 ORM doesn't expose it cleanly).
- `onboarding.public.submission.create_from_payload(payload)` (`@api.model`) — orchestrator-callable creator from public intake.
- `onboarding.public.submission.action_promote_to_journey()` — BA action: materialize partner + journey + BRD docs.
- `onboarding.public.submission.action_reject()` — mark rejected.
- Controllers (`controllers/public_intake.py`): `/onboarding/public/intake` (POST, rate-limited, Turnstile-gated), `/onboarding/public/status/<token>` (GET).

## Integration Points
- **Depends on:** `custom_brd_analyzer`, `custom_super_admin`, `custom_approval_engine`, `custom_tenant_infra`, `project`, `mail`, `portal`.
- **Inherits from:** `mail.thread`, `mail.activity.mixin` (on `onboarding.journey`). Extends `brd.document` and `brd.recommendation` to add `journey_id`.
- **Extended by:** `custom_dev_cycle` (adds `dev_cycle_id` smart button via `brd.recommendation`).
- **External calls:** Cloudflare Turnstile verification (`https://challenges.cloudflare.com/turnstile/v0/siteverify`) — soft-fail if `requests` not installed.
- **Cross-vertical:** platform onboarding plane; not customer-facing.

## Gotchas
- **`_origin_stage_cache` returns the latest transition's `to_stage` — i.e. the new stage, not the previous one**, because the transition row was just created in the same `write()`. This means `from_stage` in newly-written transitions equals `to_stage` of the freshly-created one. Read with care.
- **`_FORWARD["on_hold"]` includes a set comprehension over all non-terminal stages** — when adding a new stage, both `STAGE_SELECTION` and the resume logic update automatically, but the kanban `STAGE_TO_COLUMN` map will silently lack the new stage and default to `Intake`.
- **Stage transition append happens AFTER `super().write()`** — if `super().write` fails, no transition is logged.
- **`sync_version` bump uses `max((r.sync_version or 0) for r in self) + 1`** — for multi-record writes, all records get the same new version (loses information about individual increments).
- **`_skip_journey_sync` context flag is the only loop-prevention** — any code that writes to project tasks without setting it will trigger a journey stage move, possibly reverting an in-flight change.
- **Public intake rate-limit bucket is process-local** (`_RATE_BUCKET: dict`) — multiple Odoo workers each have their own; no Redis backing.
- **Turnstile soft-fails when `requests` not installed** — controller logs a warning and returns True (passes). Documented but easy to miss in security review.
- **`action_promote_to_journey` partner lookup is by email only** — duplicate emails across companies collapse to a single partner.
- **BRD file decode tolerates `data:...;base64,` prefix** — but no size limit, no virus scan, no max file count enforcement.
- **`onboarding.stage.transition.write()` raises `AccessError`**, not `UserError` — different error class than other append-only models in the platform.
- **`approval_request_id.ondelete="set null"`** — deleting the approval orphans the journey's reference; the wizard does not re-create it.

## Out of Scope
- **Actual provisioning** — owned by `custom_super_admin`; journey transitions only request it.
- **VPS provisioning** — owned by `custom_tenant_infra`; journey only links.
- **BRD analysis** — owned by `custom_brd_analyzer`; journey only links.
- **Approval workflow definition** — owned by `custom_approval_engine`; journey wires one in.
- **Marketing-site UI / lead capture form** — only the receiving endpoint is here.
- **Email notifications to prospects** — chatter only.
- **Conversion analytics / funnel metrics** — none built-in.
