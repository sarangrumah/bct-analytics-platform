---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_data_cleaning
manifest_version: 19.0.0.2.0
---

# custom_data_cleaning

## Purpose
CE substitute for Odoo Enterprise `data_cleaning`. Built atop CE `data_recycle`, it provides rule-driven deduplication for any model, with Indonesian-aware normalisation (phone canonicalisation to `+62…`, email lower-casing) applied **before** comparison so equivalent values bucket together.

Also exposes reusable module-level helpers `_normalize_phone_id(value)`, `_validate_nik(value)`, `_is_valid_phone_id_format(value)` used by other addons (HR, contacts, KYC).

## Business Flow
- Admin creates a `custom.dedup.rule`: pick `model_name` (technical, e.g. `res.partner`), comma-separated `match_fields`, toggle `normalize_phone_id` / `normalize_email_case`, optionally turn on `cron_active` (daily ir.cron).
- On `action_run_scan` (manual or cron): the rule reads `search_read([], match_fields + ['id'])` over the target model, builds a key tuple per record using `_normalize_value` (lower+strip default; phone canonicalised via `_normalize_phone_id` for `phone`/`mobile`/`phone_id`/`x_phone`/`x_mobile`; email lower-cased for `email`/`email_normalized`/`x_email`), and groups records by key.
- For each bucket with >1 record, the rule unlinks existing `pending` candidates (idempotent re-scan) and creates a `custom.dedup.candidate` with `res_ids_json` (JSON array of IDs), a 255-char `preview` string built from display names, and the `match_key`.
- `last_run_at` and `last_match_count` are stamped on the rule; a chatter note is posted.
- Reviewer opens a candidate, clicks `action_open_merge_wizard` → `custom.dedup.merge.wizard` (form, target=new) which guides conflict-aware merging; `action_dismiss` flips state to `dismissed`.
- Bulk normalisation: `custom.dedup.normalize.wizard` applies phone/NIK normalisation across an arbitrary model in a single pass without merging.
- Cron lifecycle: when `cron_active=True`, `_create_cron_if_active` provisions/updates an `ir.cron` row (daily) running `rule.action_run_scan()`; clearing the flag unlinks the cron. Unlinking the rule also unlinks its cron.
- Recycle presets (`data/data_recycle_presets.xml`) seed `data_recycle.model` rows for stale archived contacts, dormant draft leads, and old cancelled sales.

## Key Models
- `custom.dedup.rule` — Per-model deduplication rule with normalisation flags and optional cron.
- `custom.dedup.candidate` — A bucket of >1 duplicate IDs awaiting reviewer action.
- `custom.dedup.merge.wizard` (TransientModel) — Guided merge UI; preserves master values where conflicting.
- `custom.dedup.normalize.wizard` (TransientModel) — Bulk normaliser for phone/NIK on any model.

## Important Fields
- `custom.dedup.rule.model_name` (Char, required) — technical target.
- `custom.dedup.rule.match_fields` (Char, required) — comma-separated field names.
- `custom.dedup.rule.normalize_phone_id` (Boolean, default True) — applies `_normalize_phone_id` to phone-like fields.
- `custom.dedup.rule.normalize_email_case` (Boolean, default True) — lower-cases email-like fields.
- `custom.dedup.rule.cron_active` (Boolean, tracked) — toggles the daily cron.
- `custom.dedup.rule.cron_id` (M2o ir.cron, readonly, ondelete=set null) — owned cron handle.
- `custom.dedup.rule.last_run_at` / `last_match_count` (Datetime/Integer, readonly) — telemetry.
- `custom.dedup.candidate.res_ids_json` (Text) — JSON array of duplicate record IDs (the source of truth, not a Many2many).
- `custom.dedup.candidate.preview` (Char, 255) — human-readable head of the bucket.
- `custom.dedup.candidate.match_key` (Char, 255) — normalised key joined by `" || "`.
- `custom.dedup.candidate.state` (Selection pending/merged/dismissed).

## Public Methods
- `custom.dedup.rule.action_run_scan()` — main scan entry.
- `custom.dedup.rule._normalize_value(field_name, value)` — per-field normalisation dispatcher.
- `custom.dedup.rule._parse_match_fields()` — split + strip.
- `custom.dedup.rule._create_cron_if_active()` — cron provisioner (called from `create`/`write`).
- `custom.dedup.candidate.action_open_merge_wizard()` — launch merge.
- `custom.dedup.candidate.action_dismiss()` — false-positive flag.
- `custom.dedup.candidate._get_record_ids()` — decode `res_ids_json`.
- **Module-level helpers (importable):**
  - `custom_data_cleaning.models.custom_dedup_rule._normalize_phone_id(value)` — returns canonical `+62…` string.
  - `custom_data_cleaning.models.custom_dedup_rule._validate_nik(value)` — True if 16-digit numeric.
  - `custom_data_cleaning.models.custom_dedup_rule._is_valid_phone_id_format(value)` — True if matches `^\+62\d{8,13}$`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `data_recycle` (CE).
- **Inherits from:** `mail.thread` on `custom.dedup.rule`.
- **Extended by:** HR, contacts, KYC modules import the helper callables for input normalisation. New per-vertical rules are typically seeded via XML data, not Python.
- **External calls:** none.
- **Cross-vertical:** the `_normalize_phone_id` / `_validate_nik` helpers are the canonical Indonesian phone+NIK normalisation surface across the platform — do not re-implement.

## Gotchas
- **Scan reads ALL records of the target model** (`search_read([])`) — for very large tables this is O(N) memory. There is no batching or domain filter on the rule.
- **`res_ids_json` is opaque to the ORM** — candidates point to records via a JSON text array, not Many2many. Deleting a referenced record orphans the candidate silently; the merge wizard must validate existence.
- **Email normalisation is `lower().strip()`** — does not strip dots in gmail-style aliases or handle plus-addressing.
- **Phone normalisation assumes Indonesian numbers**: leading `0` → `+62`. International numbers from other countries will be **corrupted** (e.g. US `0xxxxxxxxxx` becomes `+62xxxxxxxxxx`). Disable `normalize_phone_id` for cross-border data.
- **`normalize_value` default else-branch is `text.lower().strip()` for ALL non-phone-non-email fields** — names/codes are lower-cased before comparison, which is desirable for fuzzy matching but means key collisions can occur between intentionally-different cased values.
- **Cron interval is hardcoded** to 1 day; no configurability per rule.
- **Re-scan is idempotent only for pending candidates** — already-merged or dismissed candidates remain; new buckets across the same data may duplicate prior dismissals.
- **`unique` constraint on rule name is NOT declared** — duplicate-named rules will silently coexist.

## Out of Scope
- **Fuzzy / Levenshtein matching** — comparison is exact-equality on normalised tuples.
- **AI-driven dedup** — not integrated; AI features live in `custom_ai_features`.
- **Cross-model dedup** — one rule = one model.
- **Audit-trail of the merge itself beyond `data_recycle` defaults** — `custom_pdp_audit` is in depends but the merge wizard's audit detail is in the wizard, not this README scope.
- **Phone validation for non-Indonesian countries** — see Gotchas.
- **NPWP / NPWP15 / passport number normalisation** — only NIK and phone helpers are exposed.
