---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_quality_full
manifest_version: 19.0.0.2.0
---

# custom_quality_full

## Purpose
Full-featured **Quality / NCR / CAPA** module that replaces the CE quality skeleton with a per-check multi-line inspection checklist, reusable test templates, tamper-evident SHA-256 e-signatures, and structured Corrective/Preventive/Containment Actions (CAPAs) with an auto-resolve cascade. Quality points define what to measure; checks execute against them; failed checks auto-raise NCR alerts; CAPAs close the loop.

## Business Flow
- Quality manager defines `quality.point` records: per-product, per-operation (incoming/manufacturing/outgoing/ad_hoc), with `check_kind` (instructions/pass_fail/measure/visual), `frequency`, optional `measure_min/max/uom`, and an optional `default_test_id` pointing to a reusable `custom.quality.test` template.
- A `custom.quality.test` template carries `custom.quality.test.line` rows (question + response_type: text/number/boolean/photo/select + is_required + expected_min/max/set).
- Operator runs a `quality.check` against a point. `name` from `ir.sequence(quality.check)`. `action_apply_test_template()` seeds `custom.quality.inspection.line` rows from `point.default_test_id` (or an explicit template).
- Per inspection line, operator fills `actual_value` / `actual_photo`; `pass_fail` is computed from response_type + expected_min/max/set + is_required.
- `overall_result` on the check rolls up required lines: `pass` if all required lines pass, `fail` if any required line fails, `na` if no required lines.
- `action_pass()` validates the measurement against `point.measure_min/max` (raises UserError if out-of-range), stamps `performed_at`, writes `pdp.audit_log`.
- `action_fail()` stamps state, then **auto-creates** a `quality.alert` (NCR) with `severity='major'` and links it via `alert_id`.
- The alert walks `open → investigating → corrective_action → resolved → closed`. CAPAs (`custom.quality.capa`) are attached: type corrective/preventive/containment, with `responsible_id`, `deadline`, `completion_date`.
- When **all** CAPAs on an alert are `done`/`canceled`, `custom.quality.capa.action_done()` cascades and auto-calls `alert.action_resolve()`.
- Signatures (`custom.quality.signature`) attach to either a check or a CAPA. On create, a SHA-256 `hash` is computed over `signer_id | check_id | capa_id | signed_at | sha256(image)`. Subsequent edits to any protected field raise `ValidationError`; `is_valid` recomputes the hash and surfaces tampering.

## Key Models
- `quality.point` — Control point definition (what / where / how).
- `quality.check` — Execution instance against a point.
- `quality.alert` — NCR (non-conformance report); auto-raised on check fail.
- `custom.quality.inspection.line` — Per-question result on a check.
- `custom.quality.test` + `custom.quality.test.line` — Reusable test/question templates.
- `custom.quality.capa` — Corrective / Preventive / Containment Action.
- `custom.quality.signature` — Tamper-evident SHA-256 e-signature for check or CAPA.

## Important Fields
- `quality.point.check_kind` (Selection: instructions/pass_fail/measure/visual) — semantic, gates measurement range validation.
- `quality.point.measure_min` / `measure_max` / `measure_uom_id` — range check in `action_pass`.
- `quality.point.default_test_id` (M2o `custom.quality.test`) — auto-seed inspection lines.
- `quality.check.state` (waiting/pass/fail).
- `quality.check.overall_result` (Selection: pass/fail/na, computed/stored) — required-line rollup.
- `quality.check.alert_id` (M2o `quality.alert`, readonly) — set by `action_fail`.
- `quality.alert.severity` (minor/major/critical) — defaults `major` from check fail.
- `quality.alert.state` (open/investigating/corrective_action/resolved/closed).
- `custom.quality.inspection.line.pass_fail` (pass/fail/na, computed) — per-response-type logic.
- `custom.quality.inspection.line.response_type` (text/number/boolean/photo/select).
- `custom.quality.capa.action_type` (corrective/preventive/containment).
- `custom.quality.signature.hash` (Char, readonly) — SHA-256; tamper detector.
- `custom.quality.signature.is_valid` (Boolean, computed) — `True` iff stored hash == recomputed hash.

## Public Methods
- `quality.check.action_pass()` — Validates measurement bounds; writes pass + audit log.
- `quality.check.action_fail()` — Writes fail + auto-creates `quality.alert` + audit log.
- `quality.check.action_apply_test_template(test_id=None)` — Seeds inspection lines from a test template.
- `quality.alert.action_investigate()` / `action_corrective()` / `action_resolve()` / `action_close()` — Workflow.
- `custom.quality.capa.action_start()` / `action_done()` / `action_cancel()` / `action_reset()` — `action_done` cascades to alert auto-resolve when all CAPAs are done/canceled.
- `custom.quality.test.apply_to_check(check)` — Copy template lines onto a live check.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `mrp`, `stock`, `mail`.
- **Inherits from:** `mail.thread` + `pdp.audited.mixin` (check), `mail.thread` + `mail.activity.mixin` + `pdp.audited.mixin` (alert), `mail.thread` + `mail.activity.mixin` (capa).
- **Extended by:** `custom_repairs` (auto-launches a `quality.check` on repair completion via `_maybe_launch_quality_check`), and any MRP/PLM vertical adding domain-specific points.
- **External calls:** none.
- **Cross-vertical:** generic Quality/NCR/CAPA capability.

## Gotchas
- **`action_fail` always creates `severity='major'` alerts** — no escalation logic; critical/minor must be set manually post-creation.
- **`overall_result='na'` when there are no required lines** — checks with only optional lines never gate; design-by-intent but easy to misread.
- **Signature tamper-detection blocks ALL writes on protected fields** — including correcting a typo in `signer_name`. Workflow expects sign-once-replace-never.
- **`custom.quality.capa.action_done` cascade calls `alert.action_resolve()`** without checking if the alert is already past `resolved`; resolution is idempotent but writes timestamps.
- **`apply_test_template` doesn't deduplicate** — calling it twice on a check appends two sets of inspection lines.
- **`pass_fail` boolean logic accepts `1, true, yes, ok, pass` as pass** — case-insensitive; anything else is fail. Free-text users may be surprised.
- **`expected_set` is a comma-separated string** (not a relational set); whitespace is stripped on compare.
- **Alert auto-creation uses `sudo()`** — bypasses ACLs; relies on the check creator having visibility downstream.

## Out of Scope
- Statistical Process Control (SPC) / control charts.
- AQL sampling tables (frequency `random` is just a marker).
- Customer-facing CAPA portal — internal-only.
- Multi-photo per inspection line — `actual_photo` is a single binary.
- Digital twin / measurement equipment integration.
