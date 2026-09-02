---
status: reviewed
generated_at: 2026-07-30T00:00:00Z
generator: hand-written
module: custom_project_cr
manifest_version: 19.0.1.0.0
---

# custom_project_cr

## Purpose
Change Request as its own record type, not `task_type = change_request`. Three things a
task does not have and which would otherwise sit empty on every task in the system: a
tiered approval gate, an impact analysis, and a response SLA counted from when the brand
asked.

## Business Flow
Intake (`approval_state = draft`, one triage queue) → BA takes it into analysis (stamps
`first_response_at` and whether the response SLA was met) → impact analysis + effort
estimate → submit for approval → tiers approve in order → approved → spawns tasks →
Waiting User Verification → closed.

Rejection needs a written reason; the constraint enforces it because the brand will ask.

## Key Models
- `custom.change.request` — `code` auto `CR-YYYY-NNNN` from `ir.sequence`, mandatory
  `vertical_id`, optional `project_id`, impact analysis fields, `approval_state`, shares
  `stage_id` (`project.task.type`) with tasks.
- `custom.change.request.approval` — one row per tier, kept as records so the decision
  trail survives. Tier N cannot approve before tier N-1.
- `project.task` (extended) — `change_request_id`, `custom_cr_code` (stored related).

## Important Fields
- `impact` — `high` / `critical` pulls in a third approver (the vertical owner);
  `_required_tiers()` is where that rule lives.
- `sla_response_due` — computed from `request_date` + working days per priority
  (`RESPONSE_SLA_DAYS`), using the portfolio module's working-day helper.
- `sla_response_met` — stamped once, at first response. Not recomputed later.

## Public Methods
- `action_start_analysis` / `action_submit_for_approval` / `action_approve` /
  `action_reject` / `action_spawn_tasks` / `action_request_verification`.
- `_cr_notify_event(event, extra=None)` — no-op hook; `custom_project_notify` overrides it.
- `_cr_external_approval_hook()` — where a `custom_approval_engine` request would be
  raised. Left as a hook so this module installs without the approval stack.
- `cron_intake_sla()` — hourly; flags intake nobody triaged inside the response SLA.

## Integration Points
- **Depends on:** `custom_project_portfolio` only.
- **Inherits:** `mail.thread`, `mail.activity.mixin`, `pdp.audited.mixin`.
- **Extended by:** `custom_project_notify`, `custom_project_api`.

## Deviation from the plan
The plan routed approvals through `ee_gap/custom_approval_engine`. This module keeps a
small native chain instead so a change request stays installable and testable without
pulling the whole approval stack into the tenant. `_cr_external_approval_hook` is the
seam for that integration when it is wanted.

## Tests
`tests/test_change_request.py` (14), tag `pmo`: numbering, intake, response SLA per
priority, the analysis gate, two vs three tiers, out-of-order approval refusal, rejection
reason, and that a spawned task carries the brand and the CR number.
