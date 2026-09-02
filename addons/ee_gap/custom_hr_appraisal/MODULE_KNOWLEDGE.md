---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_hr_appraisal
manifest_version: 19.0.0.1.0
---

# custom_hr_appraisal

## Purpose
Lightweight EE-equivalent performance appraisal: define weighted competency templates, launch periodic cycles, auto-create an `appraisal.appraisal` per in-scope employee, run self → manager → calibration → closed workflow with weighted overall score, and audit every state change as `sensitive_pii`.

## Business Flow
- HR designs an `appraisal.template` with `appraisal.template.item` children (name, competency, weight).
- HR creates an `appraisal.cycle` (name, period_start, period_end, template_id, optional `department_ids`) in `draft`.
- `action_launch()` searches active `hr.employee` (filtered by departments if any), creates one `appraisal.appraisal` per employee with the cycle's template, copying each template item into an `appraisal.line` (name, competency, weight). Cycle moves `draft`→`running`. Unique constraint `(cycle_id, employee_id)` prevents duplicates.
- Employee/manager workflow on `appraisal.appraisal`:
  - `action_start_self_review()` — `draft`→`self_review`.
  - Employee fills `line_ids.score_employee` (1-5) and `comment_employee`, `action_submit_self()` → `self_review`→`manager_review`, stamps `submitted_at_employee`.
  - Manager fills `line_ids.score_manager` (1-5) and comments, `action_submit_manager()` → `manager_review`→`calibration`, stamps `submitted_at_manager`. `overall_score` is recomputed (weighted average of `score_manager * weight / sum(weight)`).
  - HR `action_close()` → `calibration`→`closed`, stamps `closed_at`.
- Every transition writes a `pdp.audit_log` row via `pdp.audited.mixin._pdp_audit_write` with classification `sensitive_pii`.
- `appraisal.cycle.action_close()` is a free transition (no state guard) to mark the cycle done.

## Key Models
- `appraisal.template` — Reusable item set + weights, multi-company via `company_id`.
- `appraisal.template.item` — Per-template line: name, competency, weight, description.
- `appraisal.cycle` — Time-windowed campaign (`period_start`/`period_end`) with optional `department_ids` scoping; tracks count + completed_count.
- `appraisal.appraisal` — Per-employee record; inherits `mail.thread`, `mail.activity.mixin`, `pdp.audited.mixin`.
- `appraisal.line` — Per-item review row with both `score_employee` and `score_manager`.

## Important Fields
- `appraisal.appraisal.state` (Selection: draft/self_review/manager_review/calibration/closed) — drives flow; guards in `action_submit_self`/`action_submit_manager`.
- `appraisal.appraisal.overall_score` (Float, computed, stored) — `Σ(score_manager × weight) / Σ(weight)` rounded 2dp; weight=0 falls back to divisor=1.0.
- `appraisal.appraisal._uniq_cycle_employee` — `unique(cycle_id, employee_id)` constraint.
- `appraisal.line.score_employee` / `score_manager` (Integer 1-5) — no DB constraint enforcing 1-5; only the help text says so.
- `appraisal.cycle.department_ids` (M2M `hr.department`) — Empty = all departments.
- `appraisal.cycle.appraisal_count` / `completed_count` (Integer, computed, not stored) — KPI badges on cycle.
- `appraisal.appraisal.submitted_at_employee` / `submitted_at_manager` / `closed_at` (Datetime, readonly) — audit timestamps.

## Public Methods
- `appraisal.appraisal.action_start_self_review()` — draft → self_review.
- `appraisal.appraisal.action_submit_self()` — self_review → manager_review (guarded).
- `appraisal.appraisal.action_submit_manager()` — manager_review → calibration (guarded), logs overall_score.
- `appraisal.appraisal.action_close()` — → closed, stamps closed_at.
- `appraisal.appraisal._pdp_audit_classification()` → `"sensitive_pii"`.
- `appraisal.cycle.action_launch()` — Spawn appraisals for all in-scope employees, → running.
- `appraisal.cycle.action_close()` — Free transition → closed.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `hr`, `mail`.
- **Inherits from:** `mail.thread`, `mail.activity.mixin`, `pdp.audited.mixin` on `appraisal.appraisal`; `mail.thread` on `appraisal.cycle`.
- **Extended by:** none in-tree.
- **External calls:** none.
- **Cross-vertical:** generic HR capability; no Indonesian regulation tie-in.

## Gotchas
- **`overall_score` only uses `score_manager`** — `score_employee` is for self-rating display only and never enters the computed score.
- **Score range (1-5) is not enforced** at field level (no `@api.constrains`); only documented in `help`.
- **`action_launch` reads the manager via `employee.parent_id`** — if hierarchies aren't maintained on `hr.employee.parent_id`, `manager_id` ends up False and managers can't be auto-notified.
- **No reopen path** — once `closed`, there's no `action_reopen()`.
- **`appraisal.cycle.action_close()` doesn't validate** that all child appraisals are closed; cycles can close with appraisals stuck in `self_review`.
- **`appraisal.line.create` in `appraisal.create()` uses `sudo()`** — bypasses ACLs to materialise template items for the assignee.
- **PDP classification `sensitive_pii`** — performance ratings are treated at higher sensitivity than basic HR PII; downstream retention/access rules in `custom_pdp_*` should be aware.

## Out of Scope
- **360° / peer review** — only self + direct manager; no peer or skip-level reviewer fields.
- **Goals / OKR tracking** — only competency scoring.
- **Calibration session UI** — `calibration` is a state but no calibration matrix view is provided.
- **Salary/promotion linkage** — `overall_score` is computed but not piped to payroll, recruitment, or compensation modules.
- **Bell-curve / ranking distribution enforcement.**
