---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_elearning
manifest_version: 19.0.0.2.0
---

# custom_elearning

## Purpose
Extends the CE `website_slides` module with Indonesian-localized e-learning capabilities: learner cohort/batch management, Bahasa Indonesia QWeb-PDF certificate generation, course catalog filter fields (level / duration / category / certificate validity), quiz pass-threshold logic, HR department auto-enrolment, mid-point completion reminder cron, and an `hr.skill` bridge that awards skills on course completion.

Intended for SMB tenants running internal training / onboarding / compliance courses where certificate issuance and cohort-based reporting matter more than the CE website storefront.

## Business Flow
- An admin creates a `slide.channel` (course) and fills the localization fields: `x_level`, `x_duration_hours`, `x_certificate_validity_months`, `x_id_category`, `x_id_language`, optional `x_certificate_template_id` and `x_completion_appraisal_skill_code`.
- An admin creates `custom.elearning.cohort(name, channel_id, start_date, end_date, capacity, department_id)` and either manually fills `member_ids` or calls `action_auto_enrol_by_department(department_id)` — which sweeps `hr.employee` in that department and adds `work_contact_id` (preferred) or `user_id.partner_id` to `member_ids`, creating any missing `slide.channel.partner` enrolment rows.
- Learners progress through slides; `slide.channel.partner.completion` (CE field) advances. A custom `write` override on `slide.channel.partner` fires `_on_course_completed` when `completion` hits 100, which calls `_assign_hr_skill(code)` to add the configured `hr.skill` to the matching `hr.employee` (graceful no-op if `hr_skills` not installed).
- Mid-point reminder cron `slide.channel._cron_send_completion_reminders` iterates `custom.elearning.cohort` in state `open`/`running`. For each cohort past its 50% elapsed window (`_past_midpoint`), it emails every member whose `slide.channel.partner.completion < 50` via `mail_template_cohort_completion_reminder`; falls back to chatter note when the template is missing.
- Certificate issuance: `slide.channel.action_generate_certificate(partner_ids=None)` selects either explicit partners or all `completion >= 100` members, calls `slide.channel.partner._stamp_certificate_issued()` to set `x_certificate_issued / x_certificate_issue_date / x_certificate_expiry_date` (issue_date + `validity_months × 30` days), increments `x_certificate_generated_count`, and returns the `action_report_elearning_certificate` QWeb-PDF action.
- `slide.slide.check_quiz_pass(score)` accepts 0..1 or 0..100, normalises, compares to `x_passing_score` (default 70%), and posts a chatter line.

## Key Models
- `custom.elearning.cohort` — Batch/cohort with M2M members, instructor, start/end window, capacity, state machine, last_reminder_date, optional auto-enrol department.
- `slide.channel` (inherited) — Adds certificate template + counter + language, catalog filter fields (level/duration/category/validity), `x_completion_appraisal_skill_code`. Hosts `action_generate_certificate` and the cron entry point.
- `slide.channel.partner` (inherited) — Adds certificate issuance markers, computed `report_certificate_id`, `_stamp_certificate_issued`, `_on_course_completed` hook, and the `_assign_hr_skill` bridge.
- `slide.slide` (inherited) — Adds `x_passing_score` + `check_quiz_pass(score)` helper.

## Important Fields
- `custom.elearning.cohort.state` (Selection: draft/open/running/completed/cancelled, tracking) — gates cron eligibility (`open`/`running`).
- `custom.elearning.cohort.member_ids` (M2M `res.partner`) — cohort roster; also drives `enrolled_count` stored compute.
- `custom.elearning.cohort.department_id` (M2o `hr.department`) — default department for `action_auto_enrol_by_department`.
- `custom.elearning.cohort.last_reminder_date` (Date, copy=False) — bookkeeping for the mid-point reminder.
- `slide.channel.x_certificate_validity_months` (Integer, default 12) — multiplied by 30 days when stamping expiry.
- `slide.channel.x_id_language` (Selection: id/en, default `id`) — certificate render language.
- `slide.channel.x_id_category` (Selection: technical/softskill/compliance/onboarding/other).
- `slide.channel.x_completion_appraisal_skill_code` (Char) — `hr.skill.name` to assign on 100% completion.
- `slide.channel.partner.x_certificate_issued` / `x_certificate_issue_date` / `x_certificate_expiry_date` — certificate lifecycle stamps.
- `slide.slide.x_passing_score` (Float, default 70.0) — quiz pass threshold percentage.

## Public Methods
- `custom.elearning.cohort.action_auto_enrol_by_department(department_id=None)` — adds department employees as cohort members + `slide.channel.partner` rows.
- `custom.elearning.cohort.action_send_completion_reminders(force=False)` — email/chatter reminders to under-50% members; respects mid-point gate unless `force=True`.
- `custom.elearning.cohort._past_midpoint(today=None)` — boolean window check.
- `slide.channel.action_generate_certificate(partner_ids=None)` — issues certificates + returns QWeb-PDF action.
- `slide.channel._cron_send_completion_reminders()` (`@api.model`) — cron entry.
- `slide.channel.partner._stamp_certificate_issued()` — set issuance + expiry stamps (validity months × 30 days).
- `slide.channel.partner._on_course_completed()` — triggered from `write` when `completion` hits 100; calls `_assign_hr_skill`.
- `slide.channel.partner._assign_hr_skill(code)` — soft `hr.skill` bridge (no-op if models missing).
- `slide.slide.check_quiz_pass(score)` — normalised threshold compare, chatter-posts result.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `website_slides`, `custom_hr_appraisal`, `hr`, `mail`.
- **Inherits from:** `slide.channel`, `slide.channel.partner`, `slide.slide`.
- **Extended by:** none declared.
- **External calls:** none.
- **Cross-vertical:** language=`id` default, certificate report in Bahasa Indonesia; otherwise generic.
- **hr_skills:** soft-bridged via `self.env.get("hr.skill")` — no hard dependency.

## Gotchas
- **Certificate validity is computed as `months × 30 days`** — not calendar-accurate (Feb / leap years).
- **Reminder mid-point gate uses elapsed/total ≥ 0.5** — cohorts with `total <= 0` days return False (no reminders ever fire).
- **`_assign_hr_skill` is fail-silent** — missing skill / employee / models all return False without surfacing to the operator.
- **`x_completion_appraisal_skill_code` is a Char, not a FK** — manifest acknowledges `appraisal.skill` doesn't exist yet; lookup is by `hr.skill.name = code`.
- **`write` override on `slide.channel.partner`** fires `_on_course_completed` for any write that touches `completion` — even `completion=100` re-writes will re-trigger the skill assignment (idempotent thanks to `(4, id)` add-set).
- **Auto-enrol prefers `work_contact_id` over `user_id.partner_id`** — employees with neither are silently skipped.
- **Cron entry is on `slide.channel` (`_cron_send_completion_reminders`)** but the actual logic lives on `custom.elearning.cohort` — when extending, override both.
- **No PDP audit hook** despite the manifest depending on `custom_pdp_audit` — depends declared but the models don't inherit `pdp.audited.mixin`.

## Out of Scope
- Real `appraisal.skill` model integration (Char placeholder used).
- Certificate revocation / renewal workflow (expiry stamped but never enforced).
- Quiz attempt tracking / proctoring.
- Cohort-level grading rubrics.
- SCORM / xAPI integration.
- Multi-language certificate body (only id/en switch on `slide.channel.x_id_language`).
