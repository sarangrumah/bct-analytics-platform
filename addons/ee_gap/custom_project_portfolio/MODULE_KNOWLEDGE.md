---
status: reviewed
generated_at: 2026-07-30T00:00:00Z
generator: hand-written
module: custom_project_portfolio
manifest_version: 19.0.1.0.0
---

# custom_project_portfolio

## Purpose
Delta over CE `project` for the Product Owner — Value-Added Services team: brand
verticals, portfolios, weekly sprints, weekly progress reports, and the per-stage **SLA
clock** that makes Hold and Waiting-User-Verification behave differently from a label.

## Business Flow
- Master data: `custom.project.vertical` (brand) and `custom.project.portfolio`. Verticals
  seed RETAIL / GTW / POS / TENANT / JDS / CORP active, MOBILE / URBAN archived.
  `legal_entity` is filled only where confirmed (the apparel brand = Era Busana Retailindo, Example Group); blanks are deliberate, not missing data.
- A project belongs to one vertical and one portfolio. Tasks inherit the vertical from
  their parent (change request first, then project) and may only override it with a reason.
- Stage transitions book the time just spent into one of three buckets. `project.task.type`
  carries `custom_sla_clock`: `running` (team), `paused` (Hold — deducted from cycle time),
  `user_side` (waiting on the brand — booked to the user), `stopped` (closed).
- Hold demands a reason, remembers where it came from (`custom_prev_stage_id`), and is
  flagged once when it outlives `custom_hold_until`.
- Waiting User Verification sets `custom_verification_due` (working days), nudges the brand
  PIC at H+2 and H+5, then auto-closes with `custom_auto_closed=True`.
- `custom.project.sprint` is one ISO week; a cron closes Friday, opens the next week, and
  carries unfinished work forward. `custom.weekly.progress` drafts itself Friday 15:00 with
  the factual half already filled; the BA writes the blocker and next week.

## Key Models
- `custom.project.vertical` — brand axis. `pic_partner_ids` is who gets asked to verify.
- `custom.project.portfolio` — health is the worst of its projects, computed and stored.
- `project.task.type` (extended) — the stage config the plan called
  `custom.project.stage.config`. Implemented as an extension so Odoo keeps owning one
  stage engine and one kanban.
- `custom.project.sprint` — weekly, `week_code` like `2026-W31`.
- `custom.weekly.progress` — one row per (sprint × project), unique-constrained.
- `project.project` / `project.task` (extended) — vertical, health, hold, verification,
  cycle-time fields.

## Important Fields
- `project.task.custom_cycle_time_team` — elapsed minus hold minus user-wait.
- `project.task.custom_lead_time_total` — plain elapsed. Reported next to the above; the
  gap between them *is* the time the team did not own.
- `project.task.custom_hold_duration_hours` / `custom_user_wait_hours` — accumulated on
  each transition by `_pmo_book_elapsed`.
- `project.task.custom_stage_entered_at` — basis for that accumulation.
- `project.task.custom_is_blocked` — derived from Odoo's native `depend_on_ids`; this module
  deliberately adds no second blocker field.

## Public Methods
- `project.task._pmo_notify_event(event, extra=None)` — no-op hook; overridden by
  `custom_project_notify`. Keeps this module installable and testable alone.
- `project.task.action_pmo_hold / _resume / _request_verification / _verified`.
- `project.task.cron_pmo_verification / _hold_watch / _sla` (hourly / daily / hourly).
- `project.task._pmo_add_working_days(start, days)` — skips weekends and global
  `resource.calendar.leaves`.
- `project.task.type._stage_by_code(code)` — stable resolution for the API and Jira map.
- `custom.project.sprint.current_sprint()` / `cron_roll_sprint()`.
- `custom.weekly.progress.cron_draft_weekly()` / `cron_weekly_digest()` / `action_submit()`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `project`, `hr_timesheet`, `mail`.
- **Inherits:** `pdp.audited.mixin` on every model — the transaction log is the platform's
  existing hash-chained `pdp.audit_log_v`, not a new table.
- **Extended by:** `custom_project_cr` (adds `change_request_id` to tasks),
  `custom_project_notify` (implements the notify hooks), `custom_project_api` (REST).

## Gotchas
- Odoo 19 replaced `res.groups.category_id` with `privilege_id`; groups here reuse
  `custom_core.res_groups_privilege_custom_platform`.
- `res.groups.user_ids` lists **direct** members only. Anything reached through
  `implied_ids` needs `all_user_ids`.
- Views are standalone, not inherits of core project views: a changed core xmlid is the
  most common way a module stops installing on a new release.
- Odoo defaults a new task's assignee to its creator, so "unassigned" test scenarios must
  clear `user_ids` explicitly.
- Stages are global records linked to each project on create; Odoo only renders a stage in
  a project's kanban when it is linked to that project.

## Tests
`tests/test_stage_clock.py` (10) and `tests/test_weekly_progress.py` (6), tag `pmo`.
They cover the clock buckets, hold/resume round trip, auto-close, illegal transitions,
working-day maths, weekly auto-draft idempotency and sprint carry-over.
