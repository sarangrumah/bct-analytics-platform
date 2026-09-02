# Custom PDP DSAR

Data Subject Access Request handling per UU 27/2022. Subjects can request
copies of their data, rectification, or erasure; the module gathers data,
verifies identity, and produces a deliverable archive.

## Models

- `pdp.dsar.request` — state machine: `received → verifying → gathering
  → delivered` (or `rejected`). Fields: subject email / NIK /
  `partner_ref`, request type (`access` / `rectify` / `erase` /
  `portability`), evidence, decided_by, delivered_url, delivery_hash.

## Controllers

- `/dsar/request` (POST) — public portal endpoint to file a request.
  Returns a tracking token via email.

## Methods

- `_gather_subject_data(partner_id)` — walks every model with at least
  one PDP-classified field, collects related records, packages into ZIP
  (JSON manifest + attached files). Each gather operation is audited.
- `_anonymize_subject(partner_id)` — right-to-erasure helper: replaces
  PII fields with null / hashed values, appends a delete audit record.
  Rows are NOT deleted (preserves referential integrity and audit
  history).

## Security Groups

- `pdp.group_dpo` — review and process DSAR queue.

## Dependencies

- `custom_core`, `custom_pdp_taxonomy`, `custom_pdp_audit`, `custom_ai_bridge`
  (AI assistance for identity verification & data classification of
  free-text fields).

## Install

Install after the rest of `custom_pdp_*`. Configure portal endpoint URL
in Settings → Custom Platform → PDP.

## Reference

- `docs/pdp-compliance.md` § "DSAR Workflow"
