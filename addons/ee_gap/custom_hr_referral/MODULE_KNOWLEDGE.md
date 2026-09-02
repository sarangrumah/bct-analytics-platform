---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_hr_referral
manifest_version: 19.0.0.1.0
---

# custom_hr_referral

## Purpose
Employee-referral program: employees submit candidates against `referral.position` records; HR moves the candidate through `submitted → screening → interviewed → offered → hired` (or `rejected`/`withdrawn`); on `hired`, a `referral.reward` is automatically materialised at the position's `reward_amount` and goes through its own `pending → approved → paid` ledger.

## Business Flow
- HR opens a `referral.position` (name, department_id, job_id, description, `reward_amount`, currency_id), state `open`.
- Employee creates `referral.candidate` (name, email, phone, CV attachment, `position_id`, `referrer_id=self`) — defaults state `submitted`.
- HR advances state with `action_advance(target_state)` or explicit `action_mark_hired()` / `action_reject()` / `action_withdraw()`.
- `action_mark_hired()` writes `state=hired`, stamps `hired_at`, calls `_materialise_reward()` which creates a `referral.reward` (idempotent on `reward_id`) at `position_id.reward_amount` with state `pending`. Audit row classification `sensitive_pii`.
- HR/Finance on `referral.reward`: `action_approve()` (→approved, stamps approved_at) → `action_pay()` (→paid, stamps paid_at). Audit classification `financial`.
- All transitions chatter-tracked and audited via `pdp.audited.mixin._pdp_audit_write`.

## Key Models
- `referral.position` — Open requisition with bonus amount; inherits `mail.thread`, `mail.activity.mixin`.
- `referral.candidate` — Submitted candidate; inherits `mail.thread`, `mail.activity.mixin`, `pdp.audited.mixin`.
- `referral.reward` — Bonus ledger tied to candidate + referrer; inherits `pdp.audited.mixin`.

## Important Fields
- `referral.candidate.state` (Selection: submitted/screening/interviewed/offered/hired/rejected/withdrawn).
- `referral.candidate.referrer_id` (M2o `hr.employee`, required, indexed) — who claims the reward.
- `referral.candidate.cv_attachment_id` (M2o `ir.attachment`, `ondelete=set null`) — uploaded CV.
- `referral.candidate.reward_id` (M2o `referral.reward`, readonly) — materialised on hire; idempotency anchor.
- `referral.candidate.hired_at` (Datetime, readonly) — stamped by `action_mark_hired`.
- `referral.position.reward_amount` (Monetary) — per-position bonus; `0` skips reward creation.
- `referral.position.state` (Selection: open/on_hold/closed) — informational only; no enforcement on candidate creation.
- `referral.reward.state` (Selection: pending/approved/paid).
- `referral.reward.amount` (Monetary, required) — frozen from position at hire time.

## Public Methods
- `referral.candidate.action_advance(target_state: str)` — Generic transition (no guards beyond write).
- `referral.candidate.action_mark_hired()` — Idempotent (skips if already hired); stamps `hired_at`, materialises reward.
- `referral.candidate.action_reject()` / `action_withdraw()` — Terminal transitions; do not materialise a reward.
- `referral.candidate._materialise_reward()` — Internal; sudo()-creates `referral.reward` if none + position has nonzero amount.
- `referral.candidate._pdp_audit_classification()` → `"sensitive_pii"`.
- `referral.reward.action_approve()` / `action_pay()` — Reward ledger transitions.
- `referral.reward._pdp_audit_classification()` → `"financial"`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `hr`, `mail`.
- **Inherits from:** `mail.thread`, `mail.activity.mixin` on candidate + position; `pdp.audited.mixin` on candidate + reward.
- **Extended by:** none in-tree.
- **External calls:** none.
- **Cross-vertical:** generic HR capability; no link to `hr_recruitment.hr.applicant` or to payroll for the reward payout.

## Gotchas
- **Reward amount is frozen from position at hire time** — later edits to `position_id.reward_amount` do **not** propagate to existing rewards.
- **No link to `hr.applicant`** — the candidate lives in a separate model from the recruitment pipeline; the same person could exist twice (once here, once as a `hr.applicant`).
- **`reward.action_pay()` does not post to accounting** — no `account.move` integration; the "paid" state is informational. Operator must reconcile externally.
- **No referrer eligibility checks** — any active employee can be set as referrer; no rule preventing self-referral or rewarding terminated employees.
- **Currency taken from position at create time** — multi-currency setups may surprise if position currency differs from reward-payout currency expectation.
- **PII classification: `sensitive_pii` on candidate** but `financial` on reward — splits a single business event across two retention regimes.
- **`action_advance` accepts any selection value** with no guard — caller can jump `submitted→hired` without going through intermediate states.

## Out of Scope
- **Reward payout via payroll** — no auto-create of `hr.payslip.line` or similar.
- **Referrer leaderboard / gamification.**
- **Multi-referrer split rewards.**
- **Public referral portal** — no controller; candidates created via internal users only.
- **Integration with `hr_recruitment`** — separate pipeline.
