---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_mrp_plm
manifest_version: 19.0.0.1.0
---

# custom_mrp_plm

## Purpose
**Product Lifecycle Management (PLM)** layer on top of Odoo MRP. Provides Engineering Change Order (`mrp.eco`) workflow with multi-stage approval gates; on final approval, the new BoM revision is promoted to active and the old one is **archived** (`active=False`) — never deleted — preserving full audit traceability via `pdp.audited.mixin` with classification `confidential`.

## Business Flow
- Engineer creates an `mrp.eco` in `draft` against a `product.template`, captures `kind` (bom_change/product_attr/manufacturing_step), `current_bom_id`, `proposed_bom_id`, `revision` label, `reason` (HTML, required), and `impact_assessment`. `name` from `ir.sequence(mrp.eco)`.
- `action_submit()` (draft only) moves to `state=in_review` and assigns the first active `mrp.eco.stage` by sequence; writes `pdp.audit_log` action `eco_submit`.
- Reviewers iterate `action_approve()`: each call advances `stage_id` to the next active stage by sequence (audit log `eco_stage_advance`). When the current stage is `is_final=True` (or there is no next active stage), `_promote_revision()` runs:
  1. `current_bom_id.active=False` (archive)
  2. `proposed_bom_id.active=True` (promote)
  3. ECO state → `approved`, stamping `approved_by_id` + `approved_at`
  4. Audit log `eco_approved` with revision + product_tmpl payload
- `action_reject()` moves to `rejected` (any state).
- `action_cancel()` allowed unless `approved` (raises UserError if approved); audit log `eco_cancel`.
- `_group_expand_stages` ensures the kanban shows all active stages even when empty.

## Key Models
- `mrp.eco` — Engineering Change Order; the workflow record. Inherits `mail.thread`, `mail.activity.mixin`, `pdp.audited.mixin` (classification `confidential`).
- `mrp.eco.stage` — Workflow stage definition (name, sequence, is_approval, is_final, folded, active).

## Important Fields
- `mrp.eco.kind` (Selection: bom_change/product_attr/manufacturing_step) — semantic categorisation; does not change the workflow.
- `mrp.eco.product_tmpl_id` (M2o `product.template`, required, tracking) — target product.
- `mrp.eco.current_bom_id` (M2o `mrp.bom`, domain on product_tmpl_id) — soon-to-be-archived BoM.
- `mrp.eco.proposed_bom_id` (M2o `mrp.bom`) — soon-to-be-active BoM; promoted on final approval.
- `mrp.eco.revision` (Char, default `"A"`) — free-text revision label.
- `mrp.eco.reason` (HTML, required) — change rationale.
- `mrp.eco.impact_assessment` (HTML) — impact narrative.
- `mrp.eco.stage_id` (M2o `mrp.eco.stage`, group_expand) — current workflow stage.
- `mrp.eco.state` (draft/in_review/approved/rejected/cancelled).
- `mrp.eco.approved_by_id` + `approved_at` (readonly) — stamped by `_promote_revision`.
- `mrp.eco.stage.is_final` (Boolean) — triggers BoM promotion on advance.
- `mrp.eco.stage.is_approval` (Boolean) — informational; does not gate code paths.

## Public Methods
- `mrp.eco.action_submit()` — draft → in_review, set first active stage, audit log.
- `mrp.eco.action_approve()` — advance stage; if at final, promote revision.
- `mrp.eco.action_reject()` — any state → rejected, audit log.
- `mrp.eco.action_cancel()` — any non-approved state → cancelled (raises UserError if approved).
- `mrp.eco._promote_revision()` — archive current BoM, activate proposed BoM, mark approved, audit log.
- `mrp.eco._pdp_audit_classification()` — returns `"confidential"`.
- `mrp.eco._group_expand_stages(stages, domain)` — kanban stage expansion.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `mrp`, `mail`.
- **Inherits from:** `mail.thread` + `mail.activity.mixin` + `pdp.audited.mixin` (mrp.eco).
- **Extended by:** No declared extenders; verticals may add new stages via XML data or extend `_promote_revision` for stricter cutover (notify ops, freeze inventory, etc.).
- **External calls:** none.
- **Cross-vertical:** generic manufacturing capability.

## Gotchas
- **BoM promotion is unconditional once at final stage** — no check that `proposed_bom_id` is non-empty or that it shares the same `product_tmpl_id`. If `proposed_bom_id` is unset, nothing is activated and the old BoM remains active despite the ECO being marked `approved`.
- **`is_approval` flag on stages is informational only** — there is no code gate that uses it. Approval is implicit in `action_approve` being called.
- **`action_approve` advances by `sequence` strictly greater than current** — duplicate sequence values can lead to surprise jumps or skipped stages.
- **No notification on stage advance** beyond `pdp.audit_log` — `mail.activity.mixin` is inherited but no follow-up activities are scheduled.
- **Old BoM `active=False` (archive)** — Odoo will still consider it for historical pickings/work orders since `active` is just a flag. References stay intact.
- **`reason` is required HTML** — fully blank submissions are prevented but whitespace-only HTML may pass.
- **`revision` is free-text** — no sequence helper; manually managed letters/numbers.
- **No rollback action** — once promoted, reverting requires a new ECO with `current_bom_id`/`proposed_bom_id` swapped.

## Out of Scope
- BoM diff visualisation (lines added/removed/changed).
- Cross-product impact analysis (where-used).
- Document attachment policy (relies on `mail.thread` attachments).
- ECO templates / cloning.
- Numbering scheme enforcement for `revision`.
- Effectivity dates / phased rollout — promotion is immediate.
