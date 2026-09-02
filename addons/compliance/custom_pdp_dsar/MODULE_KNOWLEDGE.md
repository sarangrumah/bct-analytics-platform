---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_pdp_dsar
manifest_version: 19.0.0.2.0
---

# custom_pdp_dsar

## Purpose
Implements the UU 27/2022 **Data Subject Access Request (DSAR)** workflow: an inbound REST endpoint plus an internal model that walks every `ir.model.fields` tagged with `x_pdp_classification_id`, gathers all rows linked to the subject across models, packages them into a ZIP dossier `ir.attachment`, optionally summarises via `custom.ai`, and exposes the four DSAR kinds — access, erasure (anonymize), rectification, portability.

It is the operator-facing fulfilment surface for subject rights. Every state transition is hash-chained into `pdp.audit_log`.

## Business Flow
- Public anonymous client POSTs to `/dsar/request` (JSON-RPC, `csrf=False`) with `subject_email` (required) and optional `subject_nik`, `request_kind`. Controller best-effort-matches a `res.partner` by email and creates `pdp.dsar.request` in state `received`, returning `{ok, dsar_id, reference, state}`.
- DPO opens the record and runs `action_verify()` → state `verifying`, audit row.
- DPO runs `action_gather()`:
  1. `_gather_subject_data(partner_id)` queries `ir.model.fields` for every tagged field, groups by model, builds a heuristic domain (`id=` for `res.partner`, `partner_id=` for others, `user_id.partner_id=` fallback), `search_read`s up to 10000 rows per model.
  2. `_build_zip(data)` produces a ZIP with `manifest.json` (timestamps + model list) and one `<safe_model_name>.json` per model.
  3. `_ai_summary(data)` optionally calls `self.env["custom.ai"]._chat(...)` with the row counts (`quality="fast"`, max_tokens 512, Indonesian system prompt) — failure is swallowed.
  4. Persists the ZIP as `ir.attachment` (linked back to the DSAR record via `response_attachment_id`), state → `delivered`, stamps `delivered_at`, audit row.
- For erasure: `action_anonymize()` invokes `_anonymize_subject(partner_id)` which overwrites every tagged char/text/html field on every model with `ANON-<sha256-prefix>` and clears binary fields. Does NOT unlink rows.
- `action_reject()` moves to `rejected` with `rejection_reason`.

## Key Models
- `pdp.dsar.request` — The DSAR ticket; inherits `pdp.audited.mixin` + `mail.thread`. Tracks subject email/NIK, resolved partner, kind, state, response attachment, optional AI summary.

## Important Fields
- `pdp.dsar.request.name` (Char, default `DSAR/YYYYMMDD-HHMMSS`, readonly) — human reference exposed in the controller response.
- `pdp.dsar.request.state` (Selection: received/verifying/gathering/delivered/rejected, tracked) — workflow gate.
- `pdp.dsar.request.request_kind` (Selection: access/erasure/rectify/portability) — drives whether dossier vs anonymization runs.
- `pdp.dsar.request.subject_email` / `subject_nik` (Char, tracked) — identity claim from the request.
- `pdp.dsar.request.partner_id` (M2o `res.partner`, indexed) — resolved subject; required before `action_anonymize`.
- `pdp.dsar.request.response_attachment_id` (M2o `ir.attachment`, readonly) — the generated dossier ZIP.
- `pdp.dsar.request.ai_summary` (Text, readonly) — best-effort `custom.ai` digest of the dossier.
- `pdp.dsar.request.delivered_at` / `requested_at` (Datetime) — SLA computation source.
- `pdp.dsar.request.rejection_reason` (Text) — passed into audit row when rejecting.

## Public Methods
- `pdp.dsar.request.action_verify()` / `action_gather()` / `action_reject()` / `action_anonymize()` — state-transition buttons; each writes a `dsar` audit row with the transition payload.
- `pdp.dsar.request._gather_subject_data(partner_id)` (`@api.model`) — returns `{model_name: [{...row}, ...]}`; skips models not in registry or without a partner linkage, swallows per-model errors.
- `pdp.dsar.request._build_zip(data)` (`@api.model`) — pure helper, returns raw zip bytes.
- `pdp.dsar.request._ai_summary(data)` (`@api.model`) — best-effort, returns text or None; tries `res["text"] → res["content"] → json.dumps(res)[:1024]`.
- `pdp.dsar.request._anonymize_subject(partner_id)` (`@api.model`) — overwrites tagged char/text/html → `ANON-<digest>`, clears binary; ignores failures per model.
- Controller: `POST /dsar/request` (type=jsonrpc, auth=public, csrf=False).

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_taxonomy`, `custom_pdp_audit`, `custom_ai_bridge`.
- **Inherits from:** `pdp.audited.mixin`, `mail.thread`.
- **Extended by:** none declared; vertical modules typically just rely on tagging their PII fields so they show up in the gather/anonymize sweep.
- **External calls:** `custom.ai._chat` (optional, swallowed on failure).
- **Cross-vertical:** generic — any tenant with PII processing under UU 27/2022.

## Gotchas
- **Anonymization is irreversible and SILENT on failure** — `_anonymize_subject` writes per-model with `try/except` swallowing exceptions and just logging a warning. A partially-anonymized subject is possible; no rollback.
- **`_gather_subject_data` is heuristic** — it only finds models with one of: `id` on `res.partner`, a `partner_id` field, or `user_id.partner_id`. Models that link a subject via any other field (e.g. `customer_id`, `subject_partner_id`) are silently skipped. Audit before trusting completeness.
- **`search_read` is hard-capped at 10000 rows per model** — a heavy subject may be truncated without warning.
- **Controller `csrf=False, auth=public`** — there is no rate-limit, no captcha, no email-verification challenge. A spammer can flood the inbox of `pdp.dsar.request` records. Front with a WAF / reverse proxy filter.
- **Partner matching uses `email =ilike`** — case-insensitive but exact-string; alias `+suffix@` or whitespace will miss. Operator must resolve `partner_id` manually if unmatched.
- **AI summary uses `custom.ai._chat`**, not the gateway — if `custom_ai_bridge` is installed but `custom.ai` resolves elsewhere, the call shape may mismatch and silently return None.
- **No portability XML/CSV shape** — `portability` kind shares the same ZIP-of-JSON output as `access`; no machine-portable format negotiated.
- **This module's groups sit on its own `custom_pdp_dsar.res_groups_privilege_pdp_dsar` privilege** ("PDP DSAR") — Odoo 19 renders every group sharing one `res.groups.privilege` as a single pick-one dropdown on the user form, so a privilege shared across modules makes saving a user silently drop the other modules' groups. The privilege carries `custom_core.module_category_custom_platform`, so the selector still appears alongside the other custom modules. Do not point new groups at `custom_core.res_groups_privilege_custom_platform`.

## Out of Scope
- **Identity verification (KYC) of the requester** — this module accepts whoever calls the endpoint; identity proof workflow lives elsewhere.
- **SLA timers / dunning** — `requested_at` / `delivered_at` are captured but no countdown / breach notification exists.
- **Rectification UI** — `request_kind=rectify` is a state-machine option but there is no actual field-edit workflow attached.
- **Selective field-level erasure** — anonymization is all-or-nothing per partner across every tagged field.
- **DSAR for non-partner subjects** (e.g. an employee tied only via `hr.employee.user_id` with no partner linkage) — the heuristic will likely miss it.
