---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_esg
manifest_version: 19.0.0.1.0
---

# custom_esg

## Purpose
Standalone ESG (Environmental / Social / Governance) metric capture and sustainability-report generator for Indonesian SMB / listed companies subject to **OJK POJK 51/2017** sustainability reporting obligations. Supports POJK 51, GRI, SASB, and TCFD framework labels. Ships a GHG Scope 1/2/3 emission factor catalog, a GL-account → emission-factor mapping with an auto-collect cron that scans posted `account.move.line` rows and emits draft `custom.esg.measurement` records, a stakeholder-impact materiality assessment with quadrant compute, and a simple HTML sustainability report generator that aggregates measurements by metric category.

## Business Flow
- An admin seeds the metric catalog (`custom.esg.metric` with `code` per GRI/POJK 51, `category`, `subcategory`, `unit`). A POJK 51 seed file is shipped in `data/esg_metrics_pojk51.xml`.
- An admin populates `custom.esg.emission.factor` rows (Scope 1 / 2 / 3, `unit_of_measure`, `kg_co2_per_unit`, optional `source_reference` citation, `metric_id` linking to the target ESG metric).
- An admin maps GL accounts to factors via `custom.esg.account.mapping(account_id, factor_id, unit_cost)`. Cron `_cron_collect_emission_from_accounting` scans posted `account.move.line` rows for each active mapping. For each AML not previously processed (idempotency via `source_document = "aml:<id>"`), it computes `activity_qty = abs(aml.balance / unit_cost)` (or `aml.quantity` when `unit_cost=0`), then `value = activity_qty × kg_co2_per_unit`, and creates a draft `custom.esg.measurement` linked to the factor's metric.
- Manual capture: a user creates `custom.esg.measurement(metric_id, period_start, period_end, value, source_document, notes)` in `draft`. State machine: `draft -> validated` (`action_validate` stamps `validated_by_user_id`) -> `audited` (`action_audit`). `action_reset_draft` reverts.
- Auditor evidence: `custom_esg_measurement_ext.py` adds `x_audit_evidence` (binary attachment), `x_audit_evidence_filename`, `x_auditor_signature` (tracked Char, typically SHA-256 hex digest of the evidence file + auditor identity).
- Materiality: an admin scores each topic on `stakeholder_importance` (1-10) and `business_impact` (1-10) per `assessment_year`; stored compute `quadrant` maps to `critical` (high SH / low biz) / `important` (high / high) / `minor` (low / high) / `monitoring` (low / low). Unique per (topic, year, company).
- Report: `custom.esg.report.action_generate()` aggregates linked `measurement_ids` by metric category, renders an HTML table grouped by Environmental / Social / Governance / Other, stamps `state='published'` and `published_date`.

## Key Models
- `custom.esg.metric` — Catalog row; unique `code`, category, optional subcategory + unit.
- `custom.esg.measurement` — Period-bound value with draft/validated/audited workflow; tracking on metric/period/value/state.
- `custom.esg.measurement` (extended in `custom_esg_measurement_ext.py`) — Adds auditor evidence file + signature/hash.
- `custom.esg.emission.factor` — GHG Scope 1/2/3 kg-CO2e-per-unit catalog; unique (name, category, company).
- `custom.esg.account.mapping` — GL account → emission factor mapping; hosts the auto-collect cron.
- `custom.esg.materiality` — Stakeholder × business-impact scoring; stored compute `quadrant`; unique per (topic, assessment_year, company).
- `custom.esg.report` — Sustainability report with M2M measurements + HTML output.

## Important Fields
- `custom.esg.metric.code` (Char, required, unique) — GRI / POJK 51 identifier.
- `custom.esg.metric.category` (Selection: environmental/social/governance, required, tracking) — drives report aggregation.
- `custom.esg.measurement.state` (Selection: draft/validated/audited, tracking) — workflow.
- `custom.esg.measurement.source_document` (Char) — free-form ref; for auto-collected rows uses `aml:<id>` for idempotency.
- `custom.esg.measurement.x_audit_evidence` (Binary, attachment) + `x_auditor_signature` (Char, tracking) — auditor attestation.
- `custom.esg.emission.factor.category` (Selection: scope_1/scope_2/scope_3, required) — GHG protocol scopes.
- `custom.esg.emission.factor.kg_co2_per_unit` (Float, digits=(16,6)) — conversion coefficient.
- `custom.esg.emission.factor.metric_id` (M2o `custom.esg.metric`, ondelete='set null') — target metric for auto-collect.
- `custom.esg.account.mapping.unit_cost` (Float, digits=(16,4)) — divide `aml.balance` by this to derive activity quantity; if 0, use `aml.quantity` directly.
- `custom.esg.materiality.quadrant` (Selection: critical/important/minor/monitoring, stored compute) — threshold at score ≥ 6.
- `custom.esg.report.framework` (Selection: pojk51/gri/sasb/tcfd, default `pojk51`).
- `custom.esg.report.generated_html` (Html, readonly) — rendered output stamped by `action_generate`.

## Public Methods
- `custom.esg.measurement.action_validate()` / `action_audit()` / `action_reset_draft()` — workflow transitions.
- `custom.esg.emission.factor.compute_emission(factor_code_or_id, activity_value)` (`@api.model`) — returns `kg_co2_per_unit × activity_value`; accepts factor id or name.
- `custom.esg.account.mapping._cron_collect_emission_from_accounting()` (`@api.model`) — cron entry; idempotent via `source_document = 'aml:<id>'`. Returns count created.
- `custom.esg.materiality._compute_quadrant()` — threshold-based quadrant assignment.
- `custom.esg.report.action_generate()` — aggregate measurements by metric category, render HTML, publish.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `account`, `hr`.
- **Inherits from:** `custom.esg.measurement` (auditor-evidence extension via separate file `custom_esg_measurement_ext.py`); `mail.thread` on metric + measurement.
- **Extended by:** none declared.
- **External calls:** none — emission factors are local data; auto-collect reads ORM, no API.
- **Cross-vertical:** Indonesia-locked by POJK 51 framing but model schema is framework-agnostic (GRI/SASB/TCFD selectable).
- **`account` dependency:** required for `account.move.line` scan + `account.account` mapping target.
- **`hr` dependency:** declared but no `hr.*` model is used in the inspected files — likely intended for social-pillar metrics (headcount, training, K3) that would join `hr.employee`.

## Gotchas
- **Auto-collect uses `abs(aml.balance / unit_cost)`** — sign-aware accounting (credit vs debit) is collapsed; refunds and reversals are treated as additional emissions unless excluded by domain.
- **Idempotency key is `source_document='aml:<id>'`** but the dedupe `search` filters by `metric_id` — if a factor's `metric_id` changes between runs, the same AML can be re-emitted under a new metric.
- **`source_document` is plain Char** without an FK or index — large datasets will degrade the dedupe `search` on every cron tick.
- **Quadrant compute uses score ≥ 6** as the high/low threshold (1-10 range) — there is no midpoint configurability.
- **Report HTML is built via string concatenation** — XSS risk if metric `code`/`name`/`unit` ever come from user-controlled input (currently admin-only).
- **`unit_cost=0` falls back to `aml.quantity`** which is often 0 on journal entries that are pure monetary — verify mapping correctness or auto-collect emits zeros.
- **`action_audit` does not enforce `state='validated'` precondition** — auditing a draft is allowed.
- **`hr` depends but unused** in inspected files — may be a planned hook for social metrics.
- **Year compute on materiality defaults to current year**; back-dating an assessment requires explicit `assessment_year`.
- **Report aggregation does not sum per-metric across periods** — it lists each measurement as a row; multi-period rollups need a downstream view.

## Out of Scope
- POJK 51 XBRL submission / OJK reporting portal upload.
- GRI / SASB / TCFD machine-readable export (only HTML output).
- Carbon offset / credit accounting.
- Scope 3 supply-chain data ingestion (no supplier-survey workflow).
- Anomaly detection on measurement values.
- Approval workflow integration (uses local state machine, not `custom_approval`).
- Multi-currency emission cost accounting.
