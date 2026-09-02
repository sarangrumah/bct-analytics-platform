# Custom Approval Engine

Generic multi-tier approval workflow — model-agnostic via the
`approval.mixin` mixin. Ships ready-wired gates for `account.move`,
`purchase.order`, `sale.order`, `hr.expense`, `custom.expense.report`,
`account.analytic.line` (timesheet) and `hr.leave`. Add the mixin to any
custom model to extend.

**No manual "Request Approval" step.** Performing a document's native
confirm action (Confirm / Post / Submit / Validate) auto-submits the
approval when a matrix matches: the document shows **Waiting Approval** and
lands in the Approvals inbox. After the final tier approves, the document
**auto-proceeds** (auto-confirms) — no second click. The standalone
"Request Approval" button has been removed from the built-in documents
(the `action_request_approval` API is retained for custom models).

## Models

- `approval.matrix` — declares which records of a model need approval.
  Multiple matrices per model resolved by `priority desc`.
- `approval.matrix.tier` — ordered tiers. Approver resolution by
  `user` / `group` / `manager_of_creator` / `domain`. Each tier has
  `sla_hours` and an `on_overdue` strategy.
- `approval.request` — one per `(record × matrix)`. Walks tiers in
  sequence. State machine: `draft → pending → approved | rejected |
  cancelled` (the `pending` state is labelled **Waiting Approval** in the
  UI). Audit-logged to `pdp.audit_log`.
- `approval.request.line` — immutable history; cannot be edited or
  unlinked.
- `approval.delegation` — manual stand-in for a date window, optionally
  scoped to specific models.
- `approval.ooo` — auto-created from approved `hr.leave`. Auto-delegates
  pending approvals to the leave taker's manager (or explicit fallback).
- `approval.mixin` — attach to any model. Auto-submit pattern (preferred):
  ```python
  class MyModel(models.Model):
      _name = "my.model"
      _inherit = ["my.model", "approval.mixin"]

      def my_critical_action(self):
          # Auto-submit approval (when a matrix matches) and only run the
          # action for records that need no approval or are already approved.
          proceed = self.browse()
          for rec in self:
              if rec._approval_request_or_proceed():
                  proceed |= rec
          if proceed:
              return super(MyModel, proceed).my_critical_action()
          return True

      def _approval_on_granted(self):
          # Engine re-runs this as the requester once all tiers approve.
          return self.my_critical_action()
  ```
  The older raising gate `self._approval_check_required()` (raises
  `UserError` until approved) is still available for flows that prefer a
  hard block over auto-submit.

## SLA Escalation

- Cron `cron_approval_escalation` runs every **15 minutes**.
- For each `pending + overdue` request, applies the current tier's
  `on_overdue` action:
  - `auto_approve` — record approval, advance tier.
  - `escalate_to_next` — log escalation, advance to next tier.
  - `escalate_to_user` — reroute to fallback approver, reset due.
  - `none` — just re-notify.

## OOO + Delegation Resolution

When `_refresh_pending_approvers()` runs for a tier:

1. Raw approvers resolved via `_resolve_approvers()`.
2. For each raw approver:
   - If active **OOO** with `auto_delegate_to` → use the OOO target.
   - Else if active **delegation** (manual) → use the delegate.
   - Else use the original approver.

## Integration Gates

Each built-in document auto-submits approval on its native action and
auto-proceeds after grant (via `_approval_request_or_proceed` +
`_approval_on_granted`):

- `sale.order.action_confirm()`
- `purchase.order.button_confirm()`
- `account.move.action_post()` — auto-submit lives on the Post button;
  the low-level `_post()` keeps a raising `_approval_check_required()`
  safety-net so any *programmatic* post of an unapproved, matrix-matched
  move still blocks.
- `hr.expense.action_submit_expenses()`
- `custom.expense.report.action_submit_for_approval()` (grant advances the
  report to `approved`).
- `account.analytic.line.action_submit_validation()` (no matrix → validates
  immediately; grant validates the line).
- `hr.leave.action_confirm()` — engine runs as a pre-approval before the
  native manager-approval queue; opt-in per matrix.

Notes:
- Re-confirming after a **rejection** starts a fresh approval cycle.
- Auto-proceed runs as the original **requester** (`requested_by_id`) and is
  best-effort — a failed re-run leaves the document `approved` for a manual
  retry, never rolling back the approval decision.
- The raising `_approval_check_required()` remains on the mixin for custom
  models that prefer a hard block.

## Security Groups

- `group_approval_user` — submit requests, see own + assigned.
- `group_approval_manager` — read/write all requests (review queue).
- `group_approval_admin` — design matrices, see all delegations + OOO.

## Audit

Every state change writes to `pdp.audit_log` via
`pdp.audited.mixin._pdp_audit_write` — chained, tamper-evident.
Action names: `approval_submit`, `approval_advance`, `approval_complete`,
`approval_reject`, `approval_cancel`, `approval_overdue`.

## Portal

`/my/approvals` — inbox for the logged-in user (must be in
`pending_approver_ids`). Approve / reject with a comment.

## Notifications

Mail templates `mail_template_approval_pending` and
`mail_template_approval_overdue` are sent via `mail.thread`. WhatsApp /
Telegram delivery is left as a hook for `custom_ai_bridge` (not wired in
this iteration).

## Dependencies

- `custom_core`, `custom_pdp_taxonomy`, `custom_pdp_audit`
- Odoo: `mail`, `hr_holidays`, `account`, `purchase`, `sale`, `portal`

## Install

```bash
make install MODULE=custom_approval_engine DB=<tenant_db>
```

## Reference

- `docs/architecture.md` — workflow layer
- `docs/pdp-compliance.md` — audit chain integration
