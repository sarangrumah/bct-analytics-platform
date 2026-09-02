---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_dev_cycle
manifest_version: 19.0.1.0.0
---

# custom_dev_cycle

## Purpose
Tracks the full implementation lifecycle of every BRD-derived recommendation, from backlog through deployment, with GitHub/GitLab webhook auto-sync. Bridges three previously disconnected things: a `brd.recommendation` (what the customer needs), the resulting code (PR with CI status), and the deployment (per-environment release artifact on `custom.hub.module.deployment`). The state machine + webhook auto-transitions remove manual status updates.

## Business Flow
- A BA accepts a `brd.recommendation` → creates a `dev.cycle` (state `backlog`). `_compute_branch_suggestion` auto-fills `branch_name` as `feature/brd-<id>-<slug>`.
- Dev clicks `action_create_project_task` → ensures a `project.project` named "Dev Cycle Tasks" exists (or uses `journey_id.project_id` if exposed) and creates a linked `project.task` with description containing branch/repo/BRD references.
- Dev opens a PR. GitHub webhook POSTs to `/devcycle/webhook/github` (HMAC-validated via `X-Hub-Signature-256` against `dev_cycle.github_webhook_secret` ir.config_parameter). `_resolve_cycle` matches: existing `dev.cycle.pr.pr_url` → reuse cycle, else `branch_name` → cycle. `dev.cycle.pr.upsert_from_webhook` creates/updates the PR row with `provider`, `pr_number`, `pr_url`, `state` (draft/open/merged/closed), `ci_status` (pending/success/failure/error), `reviewers`, `merged_at`, `merged_by`.
- `_apply_state_to_cycle` auto-transitions the parent cycle: PR `open` while cycle is `in_dev` → cycle moves to `code_review`; PR `merged` + `ci_status=success` while cycle is before `deployed` → cycle moves to `deployed`. Posts a chatter note.
- GitLab equivalent at `/devcycle/webhook/gitlab` validates `X-Gitlab-Token` against `dev_cycle.gitlab_webhook_secret`.
- Manual transitions: `action_start` (→ in_dev, stamps `started_at`), `action_to_review`, `action_to_qa`, `action_to_uat`, `action_deploy`, `action_done` (stamps `completed_at`). `action_transition_state(new)` enforces `STATE_SEQUENCE = [backlog, in_dev, code_review, qa, uat, deployed, done]`: forward jumps any length, backward only one step.
- `dev.cycle.deployment` rows link a cycle to a `custom.hub.module.deployment` and `tenant.environment` with `outcome` (success/failure/rolled_back).
- `actual_md` is computed from `project_task_id.effective_hours / 8.0` when present; manually overridable.

## Key Models
- `dev.cycle` — One per BRD recommendation. State machine over `[backlog, in_dev, code_review, qa, uat, deployed, done]`. Inherits `mail.thread`.
- `dev.cycle.pr` — One per GitHub/GitLab PR. Cascade-deletes with cycle. Unique `(cycle_id, pr_url)`. Holds CI status and merge metadata.
- `dev.cycle.deployment` — One per per-environment deployment of the cycle's code. Links to `custom.hub.module.deployment` and `tenant.environment`.
- `brd.recommendation` (inherited via `brd_recommendation_extension.py`) — gets a back-reference `dev_cycle_ids` (One2many) so the BRD UI can see implementation progress.

## Important Fields
- `dev.cycle.state` (Selection backlog/in_dev/code_review/qa/uat/deployed/done, indexed, tracking) — drives `action_transition_state` rules.
- `dev.cycle.env_progress` (Selection dev/staging/uat/prod) — currently deployed-to environment (independent of `state`).
- `dev.cycle.brd_recommendation_id` (M2o brd.recommendation, set_null, indexed) — source recommendation.
- `dev.cycle.journey_id` (M2o onboarding.journey, set_null) — onboarding context.
- `dev.cycle.module_target_id` (M2o custom.hub.module.catalog, set_null) — which Hub module will be deployed.
- `dev.cycle.branch_name` (Char, computed `feature/brd-<id>-<slug>`, store, readonly=False) — git branch convention.
- `dev.cycle.repo_url` (Char) — git repo URL.
- `dev.cycle.assignee_id` (M2o res.users, tracking) — developer.
- `dev.cycle.estimate_md` / `actual_md` (Float) — man-days; actual auto-computed from linked project task hours.
- `dev.cycle.project_task_id` (M2o project.task, set_null, copy=False) — created by `action_create_project_task`.
- `dev.cycle.started_at` / `completed_at` (Datetime, readonly) — stamped by state transitions.
- `dev.cycle.pr_ids` / `deployment_ids` (One2many) + `pr_count` / `deployment_count` (computed) — smart-button counts.
- `dev.cycle.pr.pr_number` (Integer, indexed), `pr_url` (Char, required), `state` (Selection draft/open/merged/closed), `ci_status` (Selection pending/success/failure/error, indexed), `reviewers` (Char CSV), `merged_at` / `merged_by` / `last_synced_at` — PR mirror.
- `dev.cycle.deployment.outcome` (Selection success/failure/rolled_back, indexed) — per-env release result.

## Public Methods
- `dev.cycle.action_transition_state(new_state)` — validated forward/back-1 transitions; stamps `started_at` on first `in_dev`, `completed_at` on `done`; posts chatter note.
- `dev.cycle.action_start()` / `action_to_review()` / `action_to_qa()` / `action_to_uat()` / `action_deploy()` / `action_done()` — shorthand wrappers.
- `dev.cycle.action_create_project_task()` — creates `project.task` (default project "Dev Cycle Tasks") and links via `project_task_id`.
- `dev.cycle.action_open_pr_list()` / `action_open_deployment_list()` — smart-button drill-downs.
- `dev.cycle.pr.upsert_from_webhook(cycle, provider, pr_url, vals)` (`@api.model`) — idempotent webhook upsert; calls `_apply_state_to_cycle`.
- `dev.cycle.pr._apply_state_to_cycle()` — the auto-transition logic (merged+green → deployed; opened while in_dev → code_review).
- Controllers `controllers/webhook.py`: `/devcycle/webhook/github` (HMAC `X-Hub-Signature-256`), `/devcycle/webhook/gitlab` (`X-Gitlab-Token`).

## Integration Points
- **Depends on:** `project`, `mail`, `custom_brd_analyzer`, `custom_onboarding_journey`.
- **Inherits from:** `mail.thread` (on `dev.cycle`); extends `brd.recommendation` to add back-reference.
- **Extended by:** `custom_hub_console.dev_cycle.deployment` for canary deployments — `module_deployment_id` ties into hub_console's rollout state.
- **External calls:** none outbound. Inbound: GitHub + GitLab webhooks.
- **Cross-vertical:** generic platform-ops capability.

## Gotchas
- **Backward state moves are limited to one step.** Going from `deployed` straight back to `in_dev` raises `UserError`. Use `_force_stage` context? — no such escape hatch exists here (unlike `onboarding.journey`).
- **`branch_name` compute only fires when empty**; once set (manually or by compute), it's sticky even if the linked BRD's name changes.
- **`actual_md` compute is `readonly=False`** — manual override is preserved unless `effective_hours` is non-zero (then it overwrites).
- **Webhook secrets live in `ir.config_parameter`** (`dev_cycle.github_webhook_secret`, `dev_cycle.gitlab_webhook_secret`) — not Fernet-encrypted, not Vault-backed.
- **`_resolve_cycle` fallback by `branch_name`** means if two cycles share a branch (shouldn't happen, but not enforced), the first hit wins — no warning.
- **No state on `dev.cycle.deployment.outcome` transitions**: `rolled_back` is a leaf with no automation to flip the parent cycle back.
- **`(cycle_id, pr_url)` unique** but `pr_url` from GitHub vs the same PR's API URL can differ — webhook payload uses `html_url`; ensure your provider config aligns.
- **`action_create_project_task` creates a project named exactly "Dev Cycle Tasks"** if `journey_id.project_id` not set — minor stringly-typed coupling.
- **Demo data is loaded** (`data/demo_data.xml`) — in production with `--without-demo=all` this is skipped.
- **The `state` selection on `dev.cycle` mirrors `STATE_SEQUENCE`** — adding a stage requires syncing both.

## Out of Scope
- **CI execution / build trigger** — read-only mirror of CI status from webhooks; no `dev.cycle` button to kick a build.
- **Code review enforcement** — `reviewers` is a CSV string; no group gating.
- **Merge protection / branch-protection rules** — relies on GitHub/GitLab side.
- **Release notes generation** — none.
- **Time tracking** — `actual_md` is derived from `project.task.effective_hours` if any; this module doesn't capture time entries.
