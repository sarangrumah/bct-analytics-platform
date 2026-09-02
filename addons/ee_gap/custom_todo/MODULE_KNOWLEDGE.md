---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_todo
manifest_version: 19.0.0.1.0
---

# custom_todo

## Purpose
Extends CE `project_todo` with three productivity overlays on `project.task`: a pomodoro timer (`focus` → `break` → `idle` cycle with cron auto-transition), AI-powered task breakdown via `custom.ai._recommend` that creates real child tasks from the suggestion, and recurring template tasks (`custom.todo.template`, daily/weekly/monthly) that instantiate on schedule. Also ships a daily standup digest cron emailing each user their "done yesterday + in-progress today" task list.

## Business Flow
- **Pomodoro:** User clicks `action_pomodoro_start_focus` on a task → `x_pomodoro_state='focus'` + stamp `x_pomodoro_started_at`. `action_pomodoro_tick` (callable from JS heartbeat or `cron_pomodoro_tick`) checks elapsed time vs `x_pomodoro_minutes_focus` (default 25); when exceeded → `break`. `break` exceeding `x_pomodoro_minutes_break` (default 5) → `idle` (cycle complete). Each transition posts a chatter note. `action_pomodoro_done` is a manual short-circuit to `done`.
- **AI breakdown:** User clicks `action_ai_breakdown` on a task → builds `{name, description[:4000]}` payload, calls `custom.ai._recommend(model="project.task", res_id=…, payload=…)`. The response text is stored on `x_ai_breakdown`. `_parse_ai_subtasks(result)` extracts `result["subtasks"]` (list of strings OR list of dicts with `text`/`name`/`title`); each becomes a real `project.task` child via `Task.create({name, parent_id, project_id, user_ids})` inheriting the first assignee of the parent. Chatter post summarises count of subtasks created.
- **Recurring templates:** Admin creates `custom.todo.template` rows with name, description, default assignee/project, recurrence (`none`/`daily`/`weekly`/`monthly`). `action_create_task` manually instantiates a task. `cron_create_recurring_todos` (scheduler) iterates active templates and instantiates whenever `last_created_at` is empty or older than the recurrence window (1d/7d/30d). `last_created_at` is stamped after creation.
- **Standup digest:** `cron_send_daily_standup` (scheduler) iterates all active internal users (`share=False`), computes `_standup_user_summary(user, yesterday, today)` returning `(done_yesterday, in_progress_today)` recordsets via Odoo 19 state codes (`1_done`/`1_canceled` for done; `01_in_progress`/`02_changes_requested`/`03_approved` for active), and sends `custom_todo.mail_template_daily_standup` with `done_tasks`/`in_progress_tasks`/`digest_date` context. Users with no activity are skipped.

## Key Models
- `project.task` (inherited) — adds pomodoro fields + AI breakdown text + the actions.
- `custom.todo.template` — Recurring task template.

## Important Fields
- `project.task.x_pomodoro_state` (Selection idle/focus/break/done, default `idle`).
- `project.task.x_pomodoro_minutes_focus` (Integer, default 25).
- `project.task.x_pomodoro_minutes_break` (Integer, default 5).
- `project.task.x_pomodoro_started_at` (Datetime) — phase start; used by `action_pomodoro_tick`.
- `project.task.x_ai_breakdown` (Text) — last AI breakdown result text.
- `custom.todo.template.recurrence_rule` (Selection none/daily/weekly/monthly, tracked).
- `custom.todo.template.default_user_id` (M2o res.users) / `default_project_id` (M2o project.project).
- `custom.todo.template.last_created_at` (Datetime, readonly) — last instantiation stamp; gates the cron.
- `custom.todo.template.is_active` (Boolean, tracked) — cron filter.

## Public Methods
- `project.task.action_pomodoro_start_focus()` / `action_pomodoro_start_break()` / `action_pomodoro_done()` — manual transitions.
- `project.task.action_pomodoro_tick()` — elapsed-time auto-transition; returns list of `{task_id, from, to, elapsed_minutes}`.
- `project.task.cron_pomodoro_tick()` (`@api.model`) — cron entry calling tick on all active-pomodoro tasks.
- `project.task.action_ai_breakdown()` — main AI breakdown entry.
- `project.task._parse_ai_subtasks(result)` (`@staticmethod`) — tolerant subtask name extractor.
- `project.task._custom_ai_breakdown_payload()` — payload builder.
- `project.task._standup_user_summary(user, since_dt, until_dt)` (`@api.model`) — done/in-progress split.
- `project.task.cron_send_daily_standup()` (`@api.model`) — standup digest cron.
- `custom.todo.template.action_create_task()` — manual instantiation.
- `custom.todo.template.cron_create_recurring_todos()` (`@api.model`) — recurrence cron.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_ai_bridge`, `project_todo` (CE).
- **Inherits from:** `project.task` (inherit-extend); `mail.thread` on `custom.todo.template`.
- **Extended by:** none in tree.
- **External calls:** `custom.ai._recommend` (via `custom_ai_bridge`).
- **Cross-vertical:** generic personal productivity overlay; not vertical-locked.

## Gotchas
- **State codes are Odoo-19-specific:** the standup query filters on `state in (1_done, 1_canceled, 01_in_progress, 02_changes_requested, 03_approved)`. These are the new Odoo 19 task kanban_state codes; an Odoo 17/18 backport would break here.
- **Pomodoro minutes have a floor of 1** (`max(focus_minutes or 25, 1)`) — setting `0` does not disable transition.
- **AI subtask creation does not deduplicate** — re-running `action_ai_breakdown` on the same task creates a fresh batch of children every call.
- **Subtask name is truncated to 255 chars** in `_parse_ai_subtasks`.
- **`x_*` field prefix** — these are real model columns (not `studio.custom.field`-managed dynamic ones). The prefix is a naming convention only.
- **Recurrence math is calendar-naive**: weekly=7 days, monthly=30 days. No DST / month-length awareness.
- **`cron_create_recurring_todos` calls `action_create_task()` per template** without batching; for very large template counts use queue-job or split crons.
- **Standup email skips users with empty result** — silent. There is no log of "nothing to send".
- **`mail_template_daily_standup` must exist** as `xml_id`; if absent the cron logs a warning and exits. The template ships with the module but is loaded at install — fresh installs may need a one-off restart.
- **No "pause" pomodoro action** — focus → break is the only forward path; to abort, call `action_pomodoro_done` (jumps to `done`) or manually clear `x_pomodoro_state`.

## Out of Scope
- **Time-tracking / actual hours logging beyond pomodoro stamps** — `analytic.line` integration is not added here.
- **Pomodoro sound notifications / browser alerts** — server only; JS layer must do the UX.
- **Cross-task pomodoro coordination (one-at-a-time enforcement)** — multiple tasks can be in `focus` simultaneously.
- **AI-driven assignee suggestion / due-date inference** — only subtask name extraction is implemented.
- **Recurring templates with task dependencies / sub-task templates** — only top-level task instantiation; no sub-tree cloning.
- **Per-user pomodoro overrides** — `x_pomodoro_minutes_focus` is per-task, not per-user-default.
- **Standup digest with charts / metrics / streaks** — plain task lists only.
