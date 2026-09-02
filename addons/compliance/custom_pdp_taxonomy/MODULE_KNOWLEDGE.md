---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_pdp_taxonomy
manifest_version: 19.0.0.2.0
---

# custom_pdp_taxonomy

## Purpose
Provides the **PDP data classification taxonomy** that the rest of the PDP suite (`custom_pdp_audit`, `custom_pdp_masking`, `custom_pdp_retention`, `custom_pdp_dsar`) keys off. Declares `pdp.classification` (codes like `pii`, `sensitive_pii`, `financial`, `health`, `confidential`, `internal`, `public`) plus the `x_pdp_classification_id` column on `ir.model.fields` so any stored field on any model can be tagged with one classification.

This is the foundation layer — install it first, define classifications, tag fields. Downstream modules read `x_pdp_classification_id` to decide whether to mask, audit, retain, or include in DSAR exports.

## Business Flow
- `data/pdp_classification_data.xml` seeds the canonical classifications. Codes must be unique and contain no spaces (`_check_code`).
- `data/pdp_field_seed.xml` calls `pdp.classification._seed_partner_pii_fields()` to tag common `res.partner` fields (name/phone/mobile/email → `pii`; vat → `financial`). Uses **raw SQL** because Odoo 19 blocks ORM writes to base `ir.model.fields` rows.
- Operators tag additional fields via the `pdp.tag.fields.wizard` (pick model → multiselect fields → choose classification → `action_apply` writes `x_pdp_classification_id` in bulk).
- Downstream consumers query `ir.model.fields` with `("x_pdp_classification_id", "!=", False)` (e.g. `custom_pdp_audit.PdpAuditedMixin._pdp_audit_classification`, `custom_pdp_masking.PdpMaskedMixin._pdp_classified_field_map`, `custom_pdp_dsar.PdpDsarRequest._gather_subject_data`).

## Key Models
- `pdp.classification` — Master classification taxonomy: code, name, requires_consent, requires_masking, default_retention_days, color, active.
- `ir.model.fields` (inherited) — Adds `x_pdp_classification_id` M2o to expose the tag column.
- `pdp.tag.fields.wizard` (TransientModel) — Batch-tag UI: model_id + field_ids (domain-filtered) + classification_id → applies in one write.

## Important Fields
- `pdp.classification.code` (Char, unique, no-spaces) — stable string key (e.g. `pii`, `sensitive_pii`) used across modules.
- `pdp.classification.requires_consent` (Boolean) — flag for upstream gating; this module only stores it (consent check lives in `custom_pdp_consent`).
- `pdp.classification.requires_masking` (Boolean) — hint for `custom_pdp_masking`; not auto-enforced here.
- `pdp.classification.default_retention_days` (Integer, default 0) — hint for `custom_pdp_retention` policy seeding; 0 = governed elsewhere.
- `pdp.classification.color` / `active` — UX/lifecycle only.
- `ir.model.fields.x_pdp_classification_id` (M2o `pdp.classification`, `ondelete="set null"`) — the per-field tag.

## Public Methods
- `pdp.classification._seed_partner_pii_fields()` (`@api.model`) — idempotent SQL update on `ir_model_fields` for `res.partner` PII fields. Called from `<function/>` in data XML.
- `pdp.tag.fields.wizard.action_apply()` — writes `x_pdp_classification_id` on selected fields; returns a `display_notification` action.
- `pdp.tag.fields.wizard._onchange_model()` — clears `field_ids` when model changes (`[(5, 0, 0)]`).

## Integration Points
- **Depends on:** `custom_core`.
- **Inherits from:** `ir.model.fields` (adds one Many2one column).
- **Extended by:** `custom_pdp_audit`, `custom_pdp_masking`, `custom_pdp_retention`, `custom_pdp_dsar` (all consume `x_pdp_classification_id`).
- **External calls:** none.
- **Cross-vertical:** generic foundation; required for any tenant operating under UU 27/2022.

## Gotchas
- `_seed_partner_pii_fields` uses **raw SQL** (`UPDATE ir_model_fields ...`) because Odoo 19 blocks ORM writes on base `ir.model.fields`. This is intentional, not a bug — but it means cache invalidation on `ir.model.fields` may lag until next registry reload.
- The `x_pdp_classification_id` column survives module uninstall as `NULL` (because of `ondelete="set null"`), but the FK constraint itself is dropped — re-installing fresh requires re-tagging.
- No history of classification changes is kept here; only the current value. If you need an audit trail of "field was retagged from `pii` to `sensitive_pii`", rely on the `pdp.audit_log` write events from `custom_pdp_audit`.
- `pdp.classification.requires_consent` / `requires_masking` are advisory flags — this module does NOT enforce them; downstream modules choose whether to honour.
- **This module's groups sit on its own `custom_pdp_taxonomy.res_groups_privilege_pdp_core` privilege** ("PDP") — Odoo 19 renders every group sharing one `res.groups.privilege` as a single pick-one dropdown on the user form, so a privilege shared across modules makes saving a user silently drop the other modules' groups. The privilege carries `custom_core.module_category_custom_platform`, so the selector still appears alongside the other custom modules. Do not point new groups at `custom_core.res_groups_privilege_custom_platform`.

## Out of Scope
- **Field-level masking/redaction logic** — see `custom_pdp_masking`.
- **Audit trail of reads/writes** — see `custom_pdp_audit`.
- **Retention/anonymization cron** — see `custom_pdp_retention`.
- **Consent capture/withdrawal** — see `custom_pdp_consent`.
- **Auto-discovery of PII fields** (heuristic scanning of field names) — partially provided in `custom_pdp_masking` via its discovery wizard, not here.
- **Hierarchical / nested classifications** — flat list only.
