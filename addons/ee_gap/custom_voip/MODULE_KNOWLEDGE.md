---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_voip
manifest_version: 19.0.0.1.0
---

# custom_voip

## Purpose
Lightweight VoIP integration module providing a provider-agnostic abstraction over SIP/PBX backends (Asterisk AMI, generic webhook, Twilio, or manual logging only) plus a persistent call log (`voip.call`) audited under PDP as PII (phone numbers + recording URLs).

Surfaces a click-to-call button on `res.partner` and a smart-count link to the partner's call history. The actual upstream call placement is stubbed — only the bookkeeping side is implemented at this version.

## Business Flow
- An admin creates a `voip.provider` row choosing a `kind` (`manual` / `webhook` / `asterisk` / `twilio`) and optionally stores an auth token through `custom.ir.config.get_encrypted` under the key `custom_voip.auth_token.<provider_id>`.
- From a partner form a user hits "Call" -> `res.partner.action_voip_call()` -> picks the lowest-sequence active provider and `voip.call.log_outbound(...)` creates a placeholder `voip.call` row in direction `outbound`, writes a `pdp.audited.mixin` audit entry (event `voip_outbound_started`).
- The actual telephone leg is handled outside Odoo (Asterisk/Twilio). Status is reflected back manually via `action_mark_answered` / `action_mark_missed` / `action_end`.
- `action_end` stamps `ended_at` (which feeds the stored compute `duration_seconds`) and writes a second audit row `voip_call_ended` carrying `duration_seconds` + `outcome`.
- A partner's smart button `action_view_voip_calls` filters `voip.call` by `partner_id`.

## Key Models
- `voip.provider` — Per-company configuration row; stores `kind`, optional `api_base_url`, `account_sid`, `caller_id`, and computes `auth_token_set` by probing `custom.ir.config` encrypted storage.
- `voip.call` — Call log; inherits `mail.thread` + `pdp.audited.mixin` (classification `pii`). Stores direction, partner, user, other_number, timestamps, outcome, optional `recording_url`.
- `res.partner` (inherited) — Adds smart count `x_custom_voip_call_count` and the `action_voip_call` / `action_view_voip_calls` buttons.

## Important Fields
- `voip.provider.kind` (Selection: manual/webhook/asterisk/twilio) — drives downstream dispatch shape (currently all paths use the same placeholder log).
- `voip.provider.auth_token_set` (Boolean, computed) — true iff a secret exists under `custom_voip.auth_token.<id>` in `custom.ir.config`.
- `voip.call.direction` (Selection: inbound/outbound) — required.
- `voip.call.outcome` (Selection: answered/missed/voicemail/busy/failed) — settable via `action_mark_*` helpers.
- `voip.call.other_number` (Char, indexed, required) — the remote leg; the recipient on outbound or caller on inbound.
- `voip.call.duration_seconds` (Integer, stored compute from `started_at`/`ended_at`) — zero until `ended_at` is set.
- `voip.call.recording_url` (Char) — opaque link to the provider's stored recording, treated as PII.

## Public Methods
- `voip.call.log_outbound(partner_id, number, user_id=None)` (`@api.model`) — click-to-call helper; picks the lowest-sequence active provider, creates the log row, writes the audit event. Returns empty recordset if no active provider exists.
- `voip.call.action_mark_answered()` / `action_mark_missed()` / `action_end()` — manual state transitions.
- `voip.provider._ir_config_key()` — returns `custom_voip.auth_token.<id>` for encrypted token storage.
- `res.partner.action_voip_call()` — delegates to `log_outbound` using `self.phone`.
- `res.partner.action_view_voip_calls()` — opens filtered list view.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `mail`.
- **Inherits from:** `res.partner` (smart button + count); `mail.thread` + `pdp.audited.mixin` on `voip.call`.
- **Extended by:** none declared.
- **External calls:** none — provider kinds are metadata-only; no Asterisk AMI / Twilio REST client is wired yet.
- **Cross-vertical:** generic — any vertical wanting telephony hooks can extend `voip.call` or add a provider kind.

## Gotchas
- **No real telephony.** `kind` exists but no dispatcher consumes it. The module is a logging shell.
- **Click-to-call silently returns empty** when no active provider exists — no UserError surfaced to the user.
- **No inbound webhook controller** is shipped; inbound calls must be inserted directly into `voip.call`.
- **Token storage assumes `custom.ir.config.get_encrypted`** is present (from `custom_core`); the compute swallows exceptions and shows the token as not set rather than raising.
- **PDP classification is `pii`** — be aware all `voip.call` records show up in PII access audit trails.
- **No company-scoped security domain** in `ir.model.access.csv` — `company_id` is on the model but record rules are not declared here.

## Out of Scope
- Actual SIP/AMI/Twilio integration (placeholder only).
- Inbound call capture via webhook.
- Voicemail transcription / recording storage (URL is captured as-is).
- Call routing / IVR.
- Per-user provider preferences.
