# Custom PDP Core

Data classification taxonomy for UU 27/2022 (Personal Data Protection).
Foundation for all other `custom_pdp_*` modules — defines what counts as
PII / sensitive PII / financial / health data and which fields are tagged.

## Models

- `pdp.classification` — taxonomy of classifications: `public`, `internal`,
  `confidential`, `pii`, `sensitive_pii`, `financial`, `health`. Each
  carries flags `requires_consent`, `requires_masking`, and
  `default_retention_days`.
- `ir.model.fields` extension — adds `x_pdp_classification_id` so any
  field can be tagged.

## Seed Data

`data/pdp_field_seed.xml` pre-tags PII fields on `res.partner`
(`name`, `phone`, `email`, `vat`, `mobile`, `function`), `hr.employee`
(NIK, NPWP, address, bank account, KK), `res.users`
(`login`, `partner_id.email`).

## Wizards

- "Tag PII Fields" (`wizards/pdp_tag_fields_wizard_views.xml`) — batch
  apply a classification to many `ir.model.fields` records.

## Security Groups

- `pdp.group_admin` — manage classifications + tag fields.

## Dependencies

- `custom_core`

## Install

Install via Apps menu. Other PDP modules depend on this.

## Reference

- `docs/pdp-compliance.md`
- UU 27/2022 (Indonesia PDP Law)
