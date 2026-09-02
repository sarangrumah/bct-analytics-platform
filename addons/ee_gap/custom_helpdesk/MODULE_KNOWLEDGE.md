---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_helpdesk
manifest_version: 19.0.0.1.0
---

# custom_helpdesk

## Purpose
CE-targeted re-implementation of Odoo EE Helpdesk: a `helpdesk.ticket` model with state/priority/SLA/tags, `helpdesk.team` with mail-alias intake (email-to-ticket), `helpdesk.sla` policies that drive a computed deadline + warn/breach status, an AI suggested-response action via `custom_ai_bridge`, and PDP audit logging on every state transition.

This module owns the platform's canonical helpdesk ticketing model — other modules (e.g. `custom_livechat`, future field-service) escalate to `helpdesk.ticket` here.

## Business Flow
- A `helpdesk.team` is created and inherits `mail.alias.mixin`; `_alias_get_creation_values()` routes any inbound email at the team alias into a new `helpdesk.ticket` with `team_id` + default priority pre-filled.
- A ticket is created either via mail alias, manually, or by `discuss.channel.action_escalate_to_helpdesk` (custom_livechat). The sequence `helpdesk.ticket` assigns `name`; if `team_id.default_priority` is set and `priority` is not passed, that becomes the default.
- `_compute_sla` picks the SLA in this order: (1) team's `sla_id` if priority matches, (2) any active `helpdesk.sla` matching priority. `_compute_sla_deadline = create_date + time_resolve_hours`.
- `_compute_sla_status` (depends on `sla_deadline / state / resolved_date`) flags `done` when state ∈ {resolved, closed}, else `breach` if past deadline, `warn` if < 1h to deadline, else `ok`.
- The hourly cron `cron_check_sla` recomputes SLA status for all non-resolved tickets.
- State transitions: `action_set_open / action_set_pending / action_set_resolved / action_set_closed`. On entering `resolved`/`closed`, `resolved_date` is auto-stamped. Every `state` change writes a raw `pdp.audit_log` row.
- `action_ai_suggest_response()` calls `custom.ai._recommend` with subject+description+priority+tags, writes `ai_suggested_text` and posts to chatter. Errors degrade to a non-blocking notification.

## Key Models
- `helpdesk.ticket` — Core record; `mail.thread + mail.activity.mixin`, state/priority/SLA/AI fields.
- `helpdesk.team` — Inherits `mail.alias.mixin`; alias dispatches to `helpdesk.ticket`.
- `helpdesk.sla` — Policy: priority + response/resolution hour budgets.
- `helpdesk.tag` — Simple tag taxonomy (unique name).

## Important Fields
- `helpdesk.ticket.state` (Selection: new/open/pending/resolved/closed) — drives SLA done logic + audit.
- `helpdesk.ticket.priority` (Selection 0..3) — drives SLA matching.
- `helpdesk.ticket.team_id` / `assignee_id` / `partner_id` — routing fields.
- `helpdesk.ticket.sla_id` / `sla_deadline` / `sla_status` (computed, stored) — SLA enforcement state machine.
- `helpdesk.ticket.ai_suggested_text` (Text) — last AI suggestion.
- `helpdesk.ticket.resolved_date` (Datetime, auto-stamped on entering resolved/closed).
- `helpdesk.team.default_priority` / `sla_id` — team defaults applied to new tickets.
- `helpdesk.sla.time_response_hours` (default 4.0) / `time_resolve_hours` (default 24.0) — hour budgets from `create_date`.

## Public Methods
- `helpdesk.ticket.action_set_open() / action_set_pending() / action_set_resolved() / action_set_closed()` — state transitions.
- `helpdesk.ticket.action_ai_suggest_response()` — AI bridge call.
- `helpdesk.ticket.cron_check_sla()` (`@api.model`) — periodic SLA status refresh.
- `helpdesk.team._alias_get_creation_values()` — email-to-ticket routing.

## Integration Points
- **Depends on:** `custom_core`, `custom_ai_bridge`, `custom_pdp_audit`, `mail`, `project`.
- **Inherits from:** `mail.thread`, `mail.activity.mixin`, `mail.alias.mixin` (team).
- **Extended by:** `custom_livechat` (escalates `discuss.channel` → `helpdesk.ticket`).
- **External calls:** `custom.ai._recommend` (AI bridge), inbound SMTP via `mail.alias`.
- **Cross-vertical:** generic ticketing hub; this is the platform's only ticket model.

## Gotchas
- **Email-to-ticket channel is `mail.alias` only.** No WhatsApp/SMS channel here — those live in `custom_whatsapp` and only land on this model via separate code paths.
- **SLA selection prefers team default but only if priority matches** — a team's SLA can be silently ignored if a ticket's priority differs from the SLA's priority; then a generic active SLA wins.
- **`time_response_hours` is captured but never used** — only `time_resolve_hours` feeds `sla_deadline`. First-response tracking is not implemented.
- **PDP audit log uses raw SQL INSERT** (no ORM) and swallows exceptions.
- **`helpdesk.tag` declares `_name_uniq` using `models.Constraint`** (Odoo 19 style) — older lint expecting `_sql_constraints` will miss it.
- **Cron only recomputes status**; it does not auto-escalate priority on breach. Escalation is left to `base.automation` or future code.
- **Project dependency is declared but unused at runtime** — likely placeholder for future project/task linkage.

## Out of Scope
- First-response SLA tracking (only resolution SLA is enforced).
- Knowledge-base / canned-answer library (lives in `custom_livechat.canned.response`).
- Customer satisfaction rating (lives in `custom_livechat` on `discuss.channel`).
- Time-tracking / billing on tickets.
