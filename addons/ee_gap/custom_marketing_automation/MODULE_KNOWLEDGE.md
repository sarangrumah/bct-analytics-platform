---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_marketing_automation
manifest_version: 19.0.0.1.0
---

# custom_marketing_automation

## Purpose
Lightweight marketing-automation engine: define `marketing.segment` (domain on `res.partner`), build a `marketing.campaign` with ordered `marketing.campaign.step` rows (email / wait / tag), and let `_cron_tick` advance each `marketing.participant` through the steps. PDP-marketing-consent is enforced at campaign start by intersecting segment partners with `pdp.consent` records under the `consent_purpose_marketing` purpose.

This is the platform's BRD-only marketing-automation module; it is intentionally smaller than Odoo EE's marketing.automation app and uses `mail.template` for delivery (no separate channel).

## Business Flow
- A user creates a `marketing.segment` with `model_id = res.partner` and a `filter_domain` (validated by `_check_domain`).
- A user creates a `marketing.campaign` (draft), assigns the segment, and adds `marketing.campaign.step` rows ordered by `sequence`. Steps are one of `email` (uses `mail_template_id`), `wait`, `tag` (uses `partner_category_id`).
- `action_start()` resolves segment partners, optionally filters by valid marketing consent (`pdp.consent` with `purpose_id = consent_purpose_marketing` and `withdrawn_at = False`), and creates one `marketing.participant` per partner pointing at the first step.
- `_cron_tick` (scheduled action `data/ir_cron_data.xml`) selects all active participants in running campaigns whose `next_action_at <= now()` and calls `_advance()` per participant.
- `_advance()` executes the current step: email → `mail.template.send_mail(force_send=False)` plus PDP audit row; tag → adds `partner_category_id` to `res.partner.category_id`; wait → no-op. It then advances the pointer to the next step, scheduling `next_action_at = now + next_step.wait_hours` (if wait) or +1h otherwise.
- When there are no more steps, `_complete()` flips state to `completed` and stamps `completed_at`.
- `action_opt_out()` (per participant) writes `state=opted_out` and audits.
- `action_pause / action_resume / action_complete` are campaign-level state buttons.

## Key Models
- `marketing.segment` — Saved domain over `res.partner`; `resolve_partners()` returns the matching recordset.
- `marketing.campaign` — Workflow record (`draft/running/paused/completed`) with `mail.thread`.
- `marketing.campaign.step` — Ordered step row (`email/wait/tag`).
- `marketing.participant` — Per-partner walker; inherits `pdp.audited.mixin` (classification `pii`).

## Important Fields
- `marketing.segment.filter_domain` (Char, default `"[]"`) — Python list literal validated via `ast.literal_eval`.
- `marketing.campaign.state` (Selection: draft/running/paused/completed) — drives the cron's selection.
- `marketing.campaign.require_marketing_consent` (Boolean, default True) — gates participant creation by `pdp.consent`.
- `marketing.campaign.segment_id` — required link to the audience.
- `marketing.campaign.step_ids` — One2many, ordered by `sequence`.
- `marketing.campaign.step.kind` (Selection: email/wait/tag) — execution dispatch key.
- `marketing.campaign.step.mail_template_id` / `wait_hours` (default 24.0) / `partner_category_id` — per-kind payload.
- `marketing.participant.state` (Selection: active/completed/opted_out) — index `+ unique (campaign_id, partner_id)`.
- `marketing.participant.next_action_at` (Datetime, default now) — the cron's tick gate.

## Public Methods
- `marketing.segment.resolve_partners()` — return matching `res.partner` recordset.
- `marketing.campaign.action_start() / action_pause() / action_resume() / action_complete()` — workflow buttons.
- `marketing.campaign._cron_tick()` (`@api.model`) — scheduled tick; advances all due participants.
- `marketing.participant._advance() / _complete() / action_opt_out()`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_pdp_consent`, `mail`.
- **Inherits from:** `mail.thread` (campaign), `pdp.audited.mixin` (participant).
- **Extended by:** none declared.
- **External calls:** outbound email via `mail.template.send_mail` (which uses CE's `mail.mail` queue). No HTTP.
- **Cross-vertical:** generic; campaign delivery channel is **email only** via `mail.template` — WhatsApp/SMS/social are NOT wired up. Adding a new channel = new `step.kind` + handler in `_advance`.

## Gotchas
- **Single delivery channel: email** (via `mail.template`). The BRD analyzer should not propose a "WhatsApp step" here without acknowledging this is a new `step.kind`.
- **Consent filter only applies at campaign START.** Partners whose consent is withdrawn after `action_start` continue to receive emails until they `action_opt_out` or are manually unsubscribed — there is no per-tick re-check.
- **`require_marketing_consent` silently no-ops if `pdp.consent` model is missing** or `consent_purpose_marketing` xmlid is not loaded; partners are passed through unfiltered.
- **Per-tick advance is 1h** for non-wait steps — there is no "send immediately" path; every step has at least 1h gap.
- **`marketing.segment.filter_domain` is `ast.literal_eval`'d**; expressions referencing dynamic values (today, user) won't work — only literal domains.
- **No deduplication** across campaigns: a partner can be in many campaigns simultaneously; the `unique(campaign_id, partner_id)` constraint only blocks duplicates within one campaign.
- **Wait step is implemented by inflating the NEXT step's `next_action_at`**, not by sleeping at the wait step itself; the wait step is itself a no-op that immediately schedules the following step.

## Out of Scope
- Multi-channel campaigns (WhatsApp/SMS/social/push) — email only.
- A/B testing of campaign steps (use `custom_email_marketing` for per-mailing A/B).
- Conditional branching beyond linear `sequence` ordering.
- Open/click tracking — relies on whatever `mail.template` / `mass_mailing` does.
- Lead/opportunity creation as a step outcome.
