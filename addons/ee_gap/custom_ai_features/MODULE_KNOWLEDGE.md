---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_ai_features
manifest_version: 19.0.0.1.1
---

# custom_ai_features

## Purpose
Surfaces the platform's ai-gateway capabilities (provided by `custom_ai_bridge`'s `custom.ai` service) throughout the Odoo UI as concrete end-user features. It is not infrastructure — it consumes infrastructure — but it defines the **canonical UX patterns** ("Ask AI…" cog action, anomaly inbox, NLQ chat portal, document auto-classify) that BRD analyzers should map any new "AI" capability requirement onto.

Four feature surfaces are bundled: (1) per-record "Ask AI…" server actions bound to 9 key business models that open `custom.ai.recommend.wizard`; (2) nightly anomaly scan cron writing `ai.anomaly.finding` rows for triage; (3) `/ai/chat` internal portal with `ai.nlq.session` / `ai.nlq.message` history and read-only NLQ execution; (4) `document.document` create-hook that auto-suggests `pdp.classification` + tags from filename/content excerpt.

## Business Flow
- **Ask AI cog menu:** XML data (`ask_ai_actions_data.xml`) declares one `ir.actions.server` per binding model (`account.move`, `purchase.order`, `sale.order`, `res.partner`, `helpdesk.ticket`, `hr.payslip`, `custom.coretax.transaction`, `fsm.work.order`, `document.document`). Each action context-launches `custom.ai.recommend.wizard` with `default_model_name` + `default_res_id`.
- **Anomaly scan cron:** `ai.anomaly.scan._cron_run()` creates a scan row, then iterates the `SCANNERS` registry (one config dict per model). Each `_scan_model(cfg)` pulls recent records, computes a metric history list, calls `custom.ai._detect_anomaly(...)` on the gateway, and if `is_anomaly=True && score>=0.5` creates an `ai.anomaly.finding` row (state `new`, severity from gateway).
- **Finding triage:** Reviewer opens an `ai.anomaly.finding` (`new`→`triaged`→`resolved`, or `dismissed`). `action_open_source()` opens the underlying record via `res_model`/`res_id`. Each transition writes a `pdp.audited.mixin` audit row.
- **NLQ chat:** User hits `/ai/chat`, controller calls `ai.nlq.session.open_or_create_for_user()` (one rolling session per user). On POST the controller calls `session.with_user(env.user).ask(question)`; `ask()` posts the user message, calls `custom.ai._nlq(question, schema_hint, locale, user_can_view_pii)`, then `_execute_plan(plan)` runs `Model.search_read(domain, fields, limit=min(plan.limit,100))` strictly read-only, whitelisted against `ALLOWED_SCHEMA`, with PII fields stripped when user lacks `custom_pdp_masking.group_view_pii`.
- **Document auto-classify:** `document.document.create()` is overridden; after the super-create, `_ai_auto_classify()` skips records that already have `classification_id`, otherwise calls `custom.ai._classify_document(filename, mimetype, text_excerpt)` and assigns the returned `pdp.classification` code + creates/links `document.tag` rows. Plain-text/JSON/XML attachments are decoded for an 8 KB text excerpt; PDFs are skipped.

## Key Models
- `ai.anomaly.scan` — Scheduler run record (`running`/`done`/`error`) owning a One2many of findings.
- `ai.anomaly.finding` — Single flagged anomaly with severity, score, rationale, suggested action, triage state, and pointer to source record via `res_model`+`res_id`/`res_ref`.
- `ai.nlq.session` — Per-user rolling NLQ chat thread, inherits `pdp.audited.mixin`.
- `ai.nlq.message` — Persisted user/assistant message row with `plan_json` + `result_json`.
- `custom.ai` (AbstractModel inherit) — Extends `custom_ai_bridge`'s service with `_detect_anomaly`, `_classify_document`, `_nlq` POST helpers hitting `/v1/workflow/{anomaly,classify-document,nlq}`.
- `document.document` (inherit) — Adds `create()` override invoking `_ai_auto_classify`.

## Important Fields
- `ai.anomaly.finding.res_model` / `res_id` (Char/Int, indexed) — pointer to flagged record.
- `ai.anomaly.finding.res_ref` (Reference, dynamic selection) — clickable cross-model link.
- `ai.anomaly.finding.severity` (Selection info/warning/critical, tracked) — drives inbox prioritisation.
- `ai.anomaly.finding.score` (Float) — gateway confidence; findings <0.5 are dropped at creation.
- `ai.anomaly.finding.state` (Selection new/triaged/dismissed/resolved, tracked) — triage workflow.
- `ai.anomaly.finding.rationale` / `suggested_action` (Text) — gateway-produced human-readable guidance.
- `ai.nlq.session.user_id` (M2o res.users) — defines whose PII-mask group governs the schema hint.
- `ai.nlq.message.role` / `content` / `plan_json` / `result_json` / `is_error` — message + structured plan/result trace.

## Public Methods
- `ai.anomaly.scan._cron_run()` (`@api.model`) — nightly scheduler entry; iterates `SCANNERS` config.
- `ai.anomaly.scan._scan_model(cfg)` — per-model scan; calls gateway then creates findings.
- `ai.anomaly.finding.action_open_source()` — open the originating record.
- `ai.anomaly.finding.action_triage()` / `action_dismiss()` / `action_resolve()` — workflow buttons, each `_pdp_audit_write`.
- `ai.nlq.session.open_or_create_for_user()` (`@api.model`) — idempotent per-user session getter.
- `ai.nlq.session.ask(question)` — main entry; persists messages + audit row.
- `ai.nlq.session._allowed_schema_for_user()` — PII-aware schema mask.
- `ai.nlq.session._execute_plan(plan)` — read-only whitelist-guarded `search_read`.
- `custom.ai._detect_anomaly(model, res_id, metric, latest_value, history, context, locale)` — gateway POST `/v1/workflow/anomaly`.
- `custom.ai._classify_document(filename, mimetype, text_excerpt, locale)` — gateway POST `/v1/workflow/classify-document`.
- `custom.ai._nlq(question, schema_hint, locale, user_can_view_pii)` — gateway POST `/v1/workflow/nlq`.
- `document.document._ai_auto_classify()` — per-record classifier invoked from create.
- Controllers: `GET /ai/chat`, `POST /ai/chat/ask` (auth=user, website).

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_taxonomy`, `custom_pdp_audit`, `custom_ai_bridge`, `custom_approval_engine`, `custom_coretax_pajakku`, `custom_documents`, `custom_field_service`, `custom_helpdesk`, `custom_hr_payroll_id`, `mail`, `portal`, `website`.
- **Inherits from:** `custom.ai` (AbstractModel — adds 3 gateway methods), `document.document` (adds auto-classify hook), `pdp.audited.mixin` + `mail.thread` on finding/session.
- **Extended by:** any vertical that wants to add new "Ask AI" entry points should add their own `ir.actions.server` records bound to their model (no Python extension needed). New anomaly scanners are added by appending to the `SCANNERS` list in `ai_anomaly_scan.py`.
- **External calls:** all AI calls flow through `custom.ai._call()` (from `custom_ai_bridge`) → ai-gateway HTTP endpoint configured in `custom_ai_bridge`.
- **Cross-vertical:** Anchor module for any BRD line mentioning "AI assistant / AI suggest / anomaly detection / natural-language query / smart classification". Map "AI capability" requirements here first; only fork a vertical-specific AI module if the workflow is genuinely domain-locked.

## Gotchas
- **Auto-classify runs on EVERY document create** — including bulk imports — and silently swallows gateway errors. Disable by pre-setting `classification_id` (workspace default already does this).
- **NLQ schema is hard-coded in `ALLOWED_SCHEMA`**: adding a new queryable model means editing `ai_nlq_session.py`, not config. PII set is also hardcoded (`PII_FIELDS`).
- **NLQ executes via `Model.sudo().search_read`** — record rule access for the calling user is bypassed once the model is in the whitelist; only the PII-field mask and the model whitelist defend privacy.
- **Anomaly scan ignores company / multi-tenant scoping** at the registry level: the scan record gets `company_id=env.company`, but `_scan_model` uses `Model.sudo().search` with no company domain. Each tenant DB scans its own data.
- **Scan threshold is hardcoded:** `score < 0.5` drops the finding; not configurable.
- **`/ai/chat/ask` is CSRF=True POST** — internal users only; not exposed to portal customers.
- **AI ask-actions are defined as `binding_model_id` server actions** — they appear in the cog menu, NOT the chatter. Some bindings (`helpdesk_ticket`, `fsm_work_order`, `coretax_transaction`, `hr_payslip`) require their respective `custom_*` modules to be installed first or the data file load will fail.
- **AI groups are implied by `base.group_user`** (since 0.1.1): every internal user automatically gets `custom_ai_bridge.group_custom_ai_user` (gates the wizard) and `group_ai_user` (gates anomaly findings + NLQ sessions). Pattern matches Odoo's `purchase.group_send_reminder` implication. Operators can unlink per-user via UI; the noupdate=1 record preserves user overrides on upgrade. Pre-0.1.1 installs get this via `migrations/19.0.0.1.1/post-migrate.py` (idempotent backfill).
- **`ai.nlq.message` model is defined in `ai_nlq_message.py` (18 lines)** — minimal pass-through; no security on the message rows themselves beyond the session ACL.

## Out of Scope
- **The recommend wizard itself** (`custom.ai.recommend.wizard`) — lives in `custom_ai_bridge`. This module only binds it to models.
- **AI provider selection / model routing / token accounting** — handled by `custom_ai_bridge` + ai-gateway.
- **Write-side NLQ** — `_execute_plan` strictly returns rows; it never creates/updates records.
- **Vector / RAG / embedding storage** — not implemented; classification and anomaly are stateless per-call.
- **Streaming responses** — all calls are request/response; chat is page-redirect, not SSE.
- **Bulk document re-classification** — only fires on create; existing documents must be reprocessed manually.
