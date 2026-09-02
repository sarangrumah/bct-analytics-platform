---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_crm
manifest_version: 19.0.0.2.0
---

# custom_crm

## Purpose
Extends standard CE `crm.lead` with EE-equivalent lead-management features: a rules-based predictive score (0-100), an AI scoring action that delegates to `custom.ai._recommend` (custom_ai_bridge), a mock lead-enrichment action, a stub lead-mining model that fabricates `crm.lead` rows from an internal seed list (no real IAP), a token-secured webhook intake (`custom.crm.web.form.token.ingest_payload`), a WhatsApp contact field, and PDP-audit logging of salesperson reassignments.

The module is intentionally a CE-on-platform replacement for EE features (Predictive Lead Scoring, Lead Mining/IAP, Lead Enrichment) — all "AI" features tunnel through `custom_ai_bridge` so the same code path works in offline/mock mode.

## Business Flow
- A salesperson or external system creates a `crm.lead`. If created via webhook, `custom.crm.web.form.token.ingest_payload(token, data)` validates the token, increments `use_count`, sets `team_id` from the token, and returns the new lead id.
- `_compute_predictive_score` runs on each write to `email_from / phone / partner_id / source_id / medium_id / country_id` and stores `x_predictive_score` as a heuristic 30..100 number, with a +20 boost when the lead's source has historical win-rate > 50%.
- A user clicks "AI Score Lead" → `action_ai_score_lead()` packs a payload via `_custom_ai_payload`, calls `custom.ai._recommend`, writes `x_ai_score`, `x_ai_reasoning`, `x_ai_scored_date`, posts a chatter note. On bridge failure it returns a non-blocking notification.
- "Enrich Lead" → `action_enrich_lead()` calls the same bridge; on failure falls back to a deterministic mock (industry, employees, website, linkedin) and may write `website` onto the linked `res.partner`.
- "Lead Mining" → user creates `custom.crm.lead.mining.request` (draft), clicks `action_get_leads()` which creates up to `lead_number` draft `crm.lead` rows from `_MOCK_COMPANIES`, flips to `done`, bumps `credits_used`.
- On `write({"user_id": …})` the `_pdp_audit_owner_change` hook raw-inserts an `internal` classification row into `pdp.audit_log` (best-effort, swallowed on error).
- `base.automation` rules in `data/crm_automation_rules.xml` provide round-robin assignment and follow-up activity samples.

## Key Models
- `crm.lead` (inherited) — Adds AI/predictive/enrichment fields + WhatsApp number + owner-change audit + mining link.
- `custom.crm.lead.mining.request` — Draft/done state machine, mocked IAP-style credits, generates draft leads from `_MOCK_COMPANIES`.
- `custom.crm.web.form.token` — Webhook intake credential; `ingest_payload(token, data)` is the public ORM entrypoint (no controller in this module).

## Important Fields
- `crm.lead.x_predictive_score` (Float, computed, stored) — heuristic 0-100, EE-equivalent.
- `crm.lead.x_ai_score` / `x_ai_reasoning` / `x_ai_scored_date` — output of AI bridge call.
- `crm.lead.x_whatsapp_number` — Indonesian SMB outreach channel (E.164).
- `crm.lead.x_enrichment_data` (Text JSON) / `x_enriched_at` — enrichment payload trail.
- `crm.lead.x_lead_mining_request_id` (M2o) — back-link to mining request that generated this lead.
- `custom.crm.lead.mining.request.state` (draft/done) — one-shot generator; `action_get_leads` blocked when done.
- `custom.crm.lead.mining.request.lead_number` (Integer, default 3) — capped by `len(_MOCK_COMPANIES)` = 5.
- `custom.crm.web.form.token.token` (Char, unique, default `secrets.token_urlsafe(24)`) — rotated via `action_rotate_token`.
- `custom.crm.web.form.token.team_id` — default `crm.team` for ingested leads.

## Public Methods
- `crm.lead.action_ai_score_lead()` — call `custom.ai._recommend`, write score + reasoning.
- `crm.lead.action_enrich_lead()` — bridge enrichment with deterministic mock fallback.
- `crm.lead._custom_ai_payload()` / `_enrichment_payload()` — bridge serialisation helpers.
- `crm.lead._pdp_audit_owner_change(old, new)` — raw INSERT into `pdp.audit_log`.
- `custom.crm.lead.mining.request.action_get_lead_count()` — mocked estimator notification.
- `custom.crm.lead.mining.request.action_get_leads()` — creates draft leads, flips state.
- `custom.crm.web.form.token.ingest_payload(token, data)` (`@api.model`) — webhook ingestion ORM entrypoint.
- `custom.crm.web.form.token.action_rotate_token()` — rotates token.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_taxonomy`, `custom_pdp_audit`, `custom_ai_bridge`, `crm`, `mail`, `base_automation`.
- **Inherits from:** `crm.lead` (adds x_* fields).
- **Extended by:** vertical CRMs that want to add channel-specific scoring; nothing declared.
- **External calls:** `custom.ai._recommend` (custom_ai_bridge → AI gateway). No direct HTTP from this module.
- **Cross-vertical:** generic.

## Gotchas
- **`custom.crm.web.form.token` has no HTTP controller in this module** — `ingest_payload` is an ORM call meant to be invoked by another addon's controller. Just installing this module does not expose a webhook URL.
- **Lead mining is a stub.** `_MOCK_COMPANIES` is hard-coded (5 entries). `lead_number` is silently clamped to that list length.
- **Owner-change audit uses raw SQL** (`env.cr.execute INSERT INTO pdp.audit_log`) and swallows exceptions — failures only produce a warning log.
- **AI score is stored as Float on `x_ai_score`** but the AI bridge may return any shape; `_recommend` result is parsed permissively (`score / reasoning / summary / text`) and falls back to `json.dumps(result)[:1000]`.
- **Predictive score is recomputed every time the lead is created/updated** with a per-recordset cache of source win-rates — cheap on small batches, may degrade on imports.
- **No deduplication on webhook intake** — every payload makes a new `crm.lead`, even if email matches an existing one.

## Out of Scope
- **Real Odoo IAP integration** — lead mining is a stub; no credits are billed.
- **Real third-party enrichment** (Clearbit/ZoomInfo/etc.) — only the bridge or a mock.
- **CRM pipeline UI changes** beyond field placement.
- **Lead-conversion to opportunity** — relies entirely on standard CE behaviour.
