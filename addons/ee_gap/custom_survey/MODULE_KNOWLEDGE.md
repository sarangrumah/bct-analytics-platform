---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_survey
manifest_version: 19.0.0.2.0
---

# custom_survey

## Purpose
Extends CE `survey` with EE-gap SMB features: a survey-kind taxonomy (employee_pulse / customer_nps / training_feedback / exit_interview), a certification flow with passing-score + HTML certificate template + email delivery, per-question weighted scoring rolled up into `survey.user_input.x_weighted_score`, NPS summaries with promoter/passive/detractor buckets and CSV export, three-tier anonymity (fully_anonymous strips partner_id from answers), and an optional link to `appraisal.appraisal` that auto-advances appraisal state to `self_review` on first survey completion.

## Business Flow
- An admin creates a `survey.survey` and picks `x_survey_kind`. For NPS surveys they pick `x_nps_question_id` (the 0-10 numeric question). For certification surveys they enable `x_is_certification`, set `x_certification_passing_score` (% threshold), provide `x_certificate_template` (HTML with `{participant_name}, {survey_title}, {score}, {issue_date}, {valid_until}` placeholders), and `x_certificate_validity_months`. For appraisal-linked surveys they pick `x_target_appraisal_id`. Anonymity is set via `x_anonymity` (`fully_anonymous / partial / identified`).
- Per-question weight is set on `survey.question.x_score_weight`.
- A respondent fills the survey. On `_create_answer`, if the survey is `fully_anonymous` the new `survey.user_input` has `partner_id`, `email`, `nickname` zeroed (best-effort).
- On submission `_action_done` runs:
  - `_compute_weighted_score` recomputes `x_weighted_score = Σ(answer_score × weight) / Σ(max_score × weight) × 100`. `max_score` is the max positive `answer_score` among `suggested_answer_ids`; falls back to 10 for numeric scales.
  - If `x_is_certification` and score ≥ passing → `action_issue_certificate(user_input)` renders the HTML template via `.format(...)`, tries `ir.actions.report._run_wkhtmltopdf` to make a PDF (falls back to HTML attachment), attaches to the user_input, and emails the participant.
  - If `x_target_appraisal_id` is set → post a note on the appraisal; if appraisal state is `draft` flip to `self_review`.
- For NPS reporting, an admin creates a `custom.survey.nps.summary` with `survey_id`, `date_from`, `date_to`. `_compute_nps` buckets `survey.user_input.line.value_numerical_box` (or `answer_score`) per response: 9-10 → promoter, 7-8 → passive, 0-6 → detractor; `nps_score = (promoter% - detractor%)`.
- `action_export_csv` builds a CSV of selected summaries and returns a download URL.

## Key Models
- `survey.survey` (inherited) — Kind, NPS question, certification, anonymity, appraisal link.
- `survey.question` (inherited) — Adds `x_score_weight`.
- `survey.user_input` (inherited) — Adds `x_weighted_score` (computed, stored) and overrides `_action_done`.
- `custom.survey.nps.summary` — Per-survey, per-date-range NPS report row (`mail.thread`).

## Important Fields
- `survey.survey.x_survey_kind` (Selection: employee_pulse/customer_nps/training_feedback/exit_interview/other).
- `survey.survey.x_nps_question_id` (M2o `survey.question`, domain=`survey_id`) — the 0-10 question.
- `survey.survey.x_target_appraisal_id` (M2o `appraisal.appraisal`) — auto-advances draft → self_review on first completion.
- `survey.survey.x_is_certification` (Boolean) / `x_certification_passing_score` (Float, default 70.0) / `x_certificate_validity_months` (Integer, default 12) / `x_certificate_template` (Html, sanitize=False).
- `survey.survey.x_anonymity` (Selection: fully_anonymous / partial / identified, default partial).
- `survey.question.x_score_weight` (Float, default 1.0).
- `survey.user_input.x_weighted_score` (Float, computed, stored) — percentage 0..100.
- `custom.survey.nps.summary.nps_score` (Float, computed) — range -100 .. +100.
- `custom.survey.nps.summary.promoter_count / passive_count / detractor_count / response_count` (Integer, computed, stored).

## Public Methods
- `survey.survey.action_issue_certificate(user_input)` — renders HTML, creates attachment, emails recipient.
- `survey.survey._certificate_render_html(user_input)` — placeholder substitution via `str.format`.
- `survey.survey._create_answer(...)` (`@api.model_create_multi`) — overridden: strips PII when `fully_anonymous`.
- `survey.user_input._compute_weighted_score()` — weighted % roll-up.
- `survey.user_input._action_done()` — overridden: certificate + appraisal hand-off.
- `survey.user_input.action_complete()` — backward-compat alias.
- `custom.survey.nps.summary._compute_nps()` — bucketing + score.
- `custom.survey.nps.summary.action_export_csv()` — builds CSV attachment download.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `survey`, `custom_hr_appraisal`, `mail`.
- **Inherits from:** `survey.survey`, `survey.question`, `survey.user_input` (CE).
- **Extended by:** none declared.
- **External calls:** `ir.actions.report._run_wkhtmltopdf` for PDF rendering; `mail.mail` for certificate delivery.
- **Cross-vertical:** generic; appraisal link is the only direct cross-module touch.

## Gotchas
- **Certificate template uses Python `str.format`**, not QWeb — `{partner.name}` style attribute access will raise `KeyError`. Only the documented placeholders are supported.
- **`_run_wkhtmltopdf` is the legacy CE helper**; it may not exist in all environments. The code wraps it in try/except and falls back to attaching raw HTML — the resulting attachment will be `.html`, not `.pdf`.
- **`fully_anonymous` strips partner_id AFTER record creation** via a sudo write — `_create_answer`'s super may have already triggered side effects (audit, trace) with the partner attached.
- **Weighted score `max_score = 10.0` fallback** is applied whenever `suggested_answer_ids` has no positive scores. For text/free-form questions this means any positive `answer_score` divided by 10 — likely incorrect for non-numeric questions; weighting them with `x_score_weight > 0` is not recommended.
- **NPS computation looks at `survey.user_input.line.value_numerical_box`** first then falls back to `answer_score`. CE's numeric question stores values on `value_numerical_box`; if the question is a Selection of 0-10 answers, `answer_score` must be configured per option.
- **Certificate emails go via direct `mail.mail.create().send()`** — no `mail.template` is used, so subject and body don't honour i18n / per-language overrides.
- **Appraisal state hop draft → self_review** is a one-way write with `sudo()`; no permission check.
- **`x_certificate_template` is `sanitize=False`** — HTML is trusted; do not allow non-admins to edit it.

## Out of Scope
- Custom NPS bucket thresholds (9/7/0 are hard-coded).
- Multi-language certificate emails (single template per survey).
- Survey scheduling / recurrence.
- Anonymous-but-identifiable token mode (`partial` only hides from reports; the record still carries partner_id).
- Certificate revocation / re-issuance workflow.
- Per-tenant certificate signing keys.
