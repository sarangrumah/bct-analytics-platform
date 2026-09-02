---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_recruitment_id
manifest_version: 19.0.0.2.0
---

# custom_recruitment_id

## Purpose
Indonesia-localized + EE-equivalent extensions to CE `hr_recruitment`: job-board source tracking (Jobstreet/Glints/LinkedIn/Kalibrr/Direct), HMAC-SHA256-verified webhook intake for inbound applications, SHA1-based candidate dedup with duplicate pointer, one-click pre-filled `calendar.event` for interviews, Indonesia offer letter PDF with PPh 21 estimate, PDP-aware applicant retention cron that anonymises expired records (`REDACTED-<id>`) while preserving stage history, and stub auto-publish actions for Jobstreet/Glints.

## Business Flow
- HR creates `hr.job` records; toggles `x_publish_jobstreet`/`x_publish_glints`; `action_post_to_jobstreet()` / `action_post_to_glints()` generate a mock external post id (`JS-MOCK-<hex>` / `GL-MOCK-<hex>`) and store it (stub — no real API call).
- Inbound application paths:
  - **Webhook**: external partner POSTs JSON to `/custom_recruitment_id/webhook/<source>` with `X-Signature` header. Controller verifies HMAC-SHA256 against secret `custom_recruitment_id.webhook_secret_<source>` (ir.config_parameter; fail-closed on missing). Body is parsed and forwarded to `custom.recruitment.webhook.log.ingest_payload(source, data)`.
  - `_normalize_payload` maps vendor-specific JSON shapes (Jobstreet `candidate.full_name/email/phone/ref_id/job_ref`; Glints `applicant.name/email/mobile/id/job_id`; LinkedIn `applicant.firstName/lastName/emailAddress/phoneNumber/applicationId/jobPostingId`; generic) into a flat dict.
  - A `custom.recruitment.webhook.log` row is persisted with the raw payload; on success an `hr.applicant` is created with `x_job_board_source` + `x_external_id`. Best-effort `job_id` match by `hr.job.name = job_ref` or `int(job_ref)`. Failures leave `processed=False` and `error_message` set.
  - **Manual**: HR creates `hr.applicant` directly; `x_job_board_source='manual'` by default.
- On `create`/`write` of an `hr.applicant` with email or phone, `_compute_x_dedup_hash` recomputes `x_dedup_hash = SHA1(lower(email) + '|' + normalize_phone(phone))`. `_flag_if_duplicate` searches earlier applicants with the same hash and sets `x_duplicate_of` + `x_is_duplicate=True`. Phone normalisation: strips non-digit, drops leading `+`, replaces leading `0` with `62`.
- Offer letter: HR fills `x_offer_salary`, `x_offer_probation_months` (default 3), `x_offer_start_date`; `_compute_x_offer_pph21_estimated` derives a rough PPh 21 estimate via a hardcoded TER-style table (see Gotchas). `action_print_offer_letter()` renders `custom_recruitment_id.action_report_offer_letter`.
- Interview scheduling: `action_schedule_interview()` opens a `calendar.event` create form pre-filled with applicant partner + interviewer partners from `hr.job.interviewer_ids` and (if available) `hr.recruitment.source.user_id`.
- PDP retention cron `cron_purge_expired_applicants` runs (per `data/recruitment_id_cron.xml`): for `hr.applicant` with `x_pdp_retention_until < today` and `partner_name NOT LIKE 'REDACTED-%'`, anonymises `partner_name`/`email_from`/`partner_phone`/`x_external_id`/`x_dedup_hash`, posts chatter note, and inserts a `pdp.audit_log` row (classification=`pii`, reason=`PDP retention horizon reached — auto-anonymize`). Returns count.

## Key Models
- `hr.applicant` (inherited) — Adds 11 fields: source, external_id, retention, consent, dedup hash, duplicate pointer/flag, offer fields.
- `hr.job` (inherited) — Adds publish toggles + external post ids per board.
- `custom.recruitment.webhook.log` — Inbound payload log; per-source state, applicant link, error message.

## Important Fields
- `hr.applicant.x_job_board_source` (Selection: manual/jobstreet/glints/linkedin/kalibrr/direct, default manual, tracked).
- `hr.applicant.x_external_id` (Char, tracked) — vendor-side applicant id.
- `hr.applicant.x_pdp_retention_until` (Date, tracked) — drives anonymise cron.
- `hr.applicant.x_pdp_consent_given` (Boolean, default False, tracked) — explicit PDP consent; cleared on anonymise.
- `hr.applicant.x_dedup_hash` (Char, computed, **stored**, indexed) — SHA1(email + '|' + e164-ish phone).
- `hr.applicant.x_duplicate_of` (M2o `hr.applicant`, indexed, `ondelete=set null`).
- `hr.applicant.x_is_duplicate` (Boolean, tracked).
- `hr.applicant.x_offer_salary` (Monetary, currency=`x_offer_currency_id`).
- `hr.applicant.x_offer_pph21_estimated` (Monetary, computed, **not stored**) — hardcoded TER-style approximation; NOT the canonical payroll calc.
- `hr.applicant.x_offer_probation_months` (Integer, default 3).
- `hr.applicant.x_offer_start_date` (Date).
- `hr.job.x_publish_jobstreet` / `x_publish_glints` (Boolean, tracked).
- `hr.job.x_external_post_id_jobstreet` / `x_external_post_id_glints` (Char, readonly).
- `custom.recruitment.webhook.log.source` (Selection of 6 sources, required, tracked).
- `custom.recruitment.webhook.log.processed` (Boolean, tracked).
- `custom.recruitment.webhook.log.applicant_id` (M2o `hr.applicant`, `ondelete=set null`).

## Public Methods
- `hr.applicant._compute_x_dedup_hash()` — depends on `email_from`, `partner_phone`.
- `hr.applicant._flag_if_duplicate()` — Marks the new record as duplicate of the earliest match.
- `hr.applicant.action_schedule_interview()` — Pre-filled `calendar.event` create form.
- `hr.applicant.action_print_offer_letter()` — Renders `custom_recruitment_id.action_report_offer_letter`.
- `hr.applicant.cron_purge_expired_applicants()` (`@api.model`) — Anonymise + audit. Returns count.
- `hr.job.action_post_to_jobstreet()` / `action_post_to_glints()` — Stub publishers.
- `custom.recruitment.webhook.log.ingest_payload(source, data)` (`@api.model`) — Normalize + create applicant.
- Controller: `POST /custom_recruitment_id/webhook/<source>` — HMAC-verified intake.
- Module-level helpers: `_normalize_phone`, `_compute_dedup_hash`, `_normalize_payload`, `_verify_signature`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_taxonomy`, `custom_pdp_audit`, `custom_pdp_retention`, `hr_recruitment`, `calendar`, `mail`.
- **Inherits from:** `hr.applicant`, `hr.job`.
- **Extended by:** none in-tree.
- **External calls:** none direct (publish actions are stubs); inbound webhooks from job boards.
- **Cross-vertical:** Indonesia-specific recruitment capability.

## Gotchas
- **`x_offer_pph21_estimated` is a placeholder TER-style table** hardcoded in the inherit file: ≤5.4M → 0%, ≤6.2M → 0.25%, ≤10.7M → 0.5%, ≤15M → 1.75%, ≤30M → 5%, else 8%. These do NOT match the canonical TER table in `custom_hr_payroll_id`. Real payslip PPh 21 will differ — operator must understand it's indicative for the offer letter only.
- **HMAC fail-closed without secret** — missing `ir.config_parameter` `custom_recruitment_id.webhook_secret_<source>` causes 401 even for valid signatures; the secret must be pre-configured per source.
- **Signature header accepts `sha256=<hex>` or plain hex** — case-insensitive lower compare via `hmac.compare_digest`.
- **Phone normalisation is Indonesia-biased**: leading `0` is rewritten to `62`. International numbers with other country codes lose their `+` but keep their CC digits; numbers from a non-`0`-prefix country with leading 0 in a different sense (e.g. UK) will be misnormalised.
- **`x_dedup_hash` is None when both email and phone are empty** — such records skip dedup entirely.
- **Dedup is "earliest wins"** — order `create_date asc, id asc`; if the original (canonical) record itself gets later flagged as duplicate elsewhere, the chain can become inconsistent.
- **Job match by `name` then `int(id)`** — names with leading whitespace or case differences won't match; numeric job_refs that aren't real Odoo ids return empty.
- **Webhook intake creates an `hr.applicant` even on missing fields** — `partner_name` falls back to `"Webhook Applicant"`; intentional but means dashboards may show many such records.
- **`cron_purge_expired_applicants` uses raw SQL INSERT into `pdp.audit_log`** — assumes the `pdp` schema/table exists (provided by `custom_pdp_taxonomy`). The chatter note is in addition to the audit row.
- **`partner_name LIKE 'REDACTED-%'` is the idempotency guard** — if a redacted applicant is re-edited with a real name, the cron will redact again on the next run.
- **`x_external_post_id_*` are mock ids** — `JS-MOCK-` / `GL-MOCK-` prefix is a signal that no real API integration exists yet.
- **`source.user_id` field check** is defensive — `hr.recruitment.source` may or may not exist depending on installed apps.
- **`x_pdp_consent_given` is cleared on anonymisation** — once `False`, you cannot tell whether the applicant ever consented; only the audit log keeps that history.

## Out of Scope
- **Real Jobstreet/Glints/LinkedIn API publishing** — only mock external ids.
- **Canonical PPh 21 calculation on offer** — see `custom_hr_payroll_id`.
- **Reverse-merge of duplicates** — `x_duplicate_of` is set, but no auto-merge of stage history or attachments.
- **OAuth/bearer auth for webhooks** — HMAC-SHA256 only.
- **Per-source webhook payload schema enforcement** — best-effort mapping; unknown fields ignored silently.
- **Multi-tenant secret rotation tooling.**
