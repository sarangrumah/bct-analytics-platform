---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_expenses
manifest_version: 19.0.0.2.0
---

# custom_expenses

## Purpose
Extends Odoo's `hr_expense` application with the EE-gap features the platform needs: AI-assisted receipt OCR (vendor / amount / date / tax / currency / confidence) via `custom_ai_bridge`, multi-tier approval workflow via `custom_approval_engine.approval.mixin`, expense-report batching (`custom.expense.report`), corporate card registry (`custom.expense.corporate.card`) with PAN masking validation, mileage tracking with configurable per-km rate, and reimbursement payment generation. PDP audit on submit/approve.

This is the canonical "claim / klaim biaya / reimbursement" module. Any BRD with "expense claim", "OCR struk", "corporate card", "mileage", "expense approval", or "bulk reimbursement" maps here.

## Business Flow
- **Card setup**: HR creates `custom.expense.corporate.card` with `employee_id` + `bank_journal_id` + `masked_number` (e.g. `**** **** **** 1234`). Validation `_check_masked_number` rejects strings that look like a full PAN (13–19 digits with no `*`).
- **Mileage product**: a `product.product` with `default_code = "MILEAGE"` triggers `x_is_mileage=True` (computed). Default per-km rate from `ir.config_parameter "custom_expenses.id_mileage_rate"` (default 5000 IDR/km).
- **Expense capture**: User attaches receipt (image/PDF) and clicks `action_ai_extract_receipt`. Method builds payload via `_custom_ai_payload()` — encodes primary attachment as base64 (priority: `message_main_attachment_id` → latest of `attachment_ids` → most recent `ir.attachment` on record) — calls `env['custom.ai']._recommend(model='hr.expense', res_id=self.id, payload={task:'extract_receipt', image_base64, ...})`. Response parsed by `_parse_ai_receipt_response(result)` and written to `x_ai_extracted_amount` / `_tax_amount` / `_date` / `_vendor` / `_currency_code` / `x_ai_confidence` / `x_receipt_ocr_text`. Failure surfaces as warning notification, never blocks.
- **Mileage**: `_onchange_mileage` and `write()` keep `total_amount = x_mileage_km * x_mileage_rate` in sync. `quantity = km`, `unit_amount = rate`.
- **Corporate card linkage**: `_onchange_corporate_card` + `_apply_corporate_card_payment_mode(vals)` force `payment_mode = "company_account"` when `x_corporate_card_id` is set — excludes from employee reimbursement queue.
- **Approval**: `hr.expense._inherit = ["hr.expense", "approval.mixin"]`. `action_submit_expenses()` overridden to partition via `_approval_request_or_proceed()` — auto-creates + submits the approval when a matrix matches (expense waits, Waiting Approval), and for the proceeding subset audits `_pdp_audit_expense_event("submit")` then super-submits. After full approval the engine re-runs `action_submit_expenses` via `_approval_on_granted`. The standalone "Request Approval" button (`action_request_approval_expense`) has been removed from the view (the method is retained).
- **Expense report**: `custom.expense.report` batches expenses for one employee. State machine `draft` → `submitted` → `approved` → `paid` (+ `cancelled`). `action_submit_for_approval()` flips state + calls `_approval_request_or_proceed()` (no-op when no matrix). `_approval_on_granted()` → `action_approve()` auto-advances the report to `approved` once all tiers approve; `action_approve()` itself remains gated by `_approval_check_required()` for the no-matrix manual path. `action_register_payment()` (approved only) creates ONE `account.payment` per report on `partner = employee_id.work_contact_id`, summing only expenses without corporate card and not `company_account` mode. All-corporate-card reports go directly to paid without payment.
- **Single-expense reimbursement**: `hr.expense.action_register_reimbursement_payment()` creates `account.payment(outbound, supplier, amount=total_amount)` on `employee.work_contact_id` (or `user_id.partner_id` fallback) — only when approval state=='approved', no corporate card, and payment_mode ≠ company_account.
- **PDP audit**: `_pdp_audit_expense_event(event)` direct INSERT to `pdp.audit_log` (classification='internal'), best-effort.

## Key Models
- `hr.expense` (inherited) — mixes `approval.mixin`; adds AI OCR fields, corporate card link, mileage, reimbursement helper.
- `custom.expense.report` — Batch container. Inherits `mail.thread`, `approval.mixin`. Sequence `custom.expense.report`.
- `custom.expense.corporate.card` — Card registry. Inherits `mail.thread`.

## Important Fields
- `hr.expense.x_receipt_ocr_text` (Text) — raw OCR text, capped 65000.
- `hr.expense.x_ai_extracted_amount` / `_tax_amount` (Monetary, currency=`currency_id`) — AI numbers, separate from user-entered `total_amount`.
- `hr.expense.x_ai_extracted_date` (Date) / `_vendor` (Char) / `_currency_code` (Char size=8).
- `hr.expense.x_ai_confidence` (Float, digits=(3,2)) — 0.0–1.0 confidence score.
- `hr.expense.x_corporate_card_id` (M2o `custom.expense.corporate.card`) — when set, `payment_mode` is forced to `company_account` on create/write.
- `hr.expense.x_is_mileage` (Boolean, computed from `product_id.default_code == "MILEAGE"`, stored).
- `hr.expense.x_mileage_km` (Float, digits=(12,2)) / `x_mileage_rate` (Monetary).
- `hr.expense.x_custom_approval_request_id` (from `approval.mixin`, M2o, computed, stored).
- `hr.expense.x_custom_approval_state` (from `approval.mixin`, related, stored).
- `custom.expense.report.state` (Selection draft/submitted/approved/paid/cancelled, required, tracking).
- `custom.expense.report.employee_id` (M2o `hr.employee`, required) — `expense_ids` constrained to same employee via `_check_expenses_same_employee`.
- `custom.expense.report.expense_ids` (M2m `hr.expense` via `custom_expense_report_expense_rel`).
- `custom.expense.report.total_amount` (Monetary, computed=`sum(expense_ids.total_amount)`, stored).
- `custom.expense.report.payment_ids` (M2m `account.payment`) — generated reimbursements.
- `custom.expense.corporate.card.masked_number` (Char, required, tracking) — `_check_masked_number` blocks PAN-shaped strings.
- `custom.expense.corporate.card.bank_journal_id` (M2o `account.journal`, type∈bank/cash, required).
- Unique constraint on card: `unique(employee_id, masked_number, company_id)`.
- Config param: `custom_expenses.id_mileage_rate` (default 5000.0 IDR/km).

## Public Methods
- `hr.expense.action_ai_extract_receipt()` — runs AI OCR, writes x_ai_* fields.
- `hr.expense._custom_ai_payload()` — base64-encodes primary receipt + metadata.
- `hr.expense._get_primary_receipt_attachment()` — attachment resolution.
- `hr.expense._parse_ai_receipt_response(result)` (static) — translates gateway response to field vals.
- `hr.expense.action_submit_expenses()` (overridden, auto-submits approval) / `_approval_on_granted()` (re-runs submit after grant). `action_request_approval_expense()` retained (button removed from view).
- `hr.expense.action_register_reimbursement_payment()` — single-expense `account.payment` generation.
- `hr.expense._pdp_audit_expense_event(event)` — best-effort `pdp.audit_log` insert.
- `hr.expense._default_mileage_rate()` (`@api.model`) — reads config param.
- `hr.expense._apply_corporate_card_payment_mode(vals)` (static) / `_apply_mileage_total(vals)` (static).
- `custom.expense.report.action_submit_for_approval()` / `action_approve()` / `action_cancel()` / `action_reset_to_draft()` / `action_register_payment()`.
- `custom.expense.corporate.card.action_view_expenses()`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_ai_bridge`, `custom_approval_engine`, `hr_expense`, `account`, `product`, `mail`.
- **Inherits from:** `hr.expense` (+ `approval.mixin`); `custom.expense.report` mixes `mail.thread` + `approval.mixin`; `custom.expense.corporate.card` mixes `mail.thread`.
- **Extended by:** verticals can override `_custom_ai_payload` or `_parse_ai_receipt_response` to customise the AI flow.
- **External calls:** AI gateway via `custom.ai._recommend` (vision OCR).
- **Cross-vertical:** generic.

## Gotchas
- **AI extraction does NOT overwrite `total_amount`** — only fills `x_ai_extracted_amount`. Operator must manually accept/edit before submit. This is deliberate to preserve user authority over the booked amount.
- **PAN check is regex-shape based** — a string like `5500 0000 0000 0000` (real-looking) is blocked, but obfuscated forms like `5500-XXXX-XXXX-0000` pass even though they contain a partial PAN.
- **Approval matrix is OPTIONAL** — `action_submit_for_approval` calls the non-raising `_approval_request_or_proceed()` (no-op when no matrix); state flips to submitted regardless. With a matrix the report waits and `_approval_on_granted` → `action_approve` advances it to `approved` automatically; without one, `action_approve` is the manual path (`_approval_check_required` returns True when no matrix matches).
- **Reimbursement requires `employee.work_contact_id`** — fallback to `user_id.partner_id` is single-expense only. Report-level register_payment fails hard.
- **Mileage compute via `default_code == "MILEAGE"`** is case-insensitive (`.upper()`). Translated product codes will not match.
- **`_apply_mileage_total(vals)` runs only at create** — write-time mileage total updates only when `x_mileage_km` or `x_mileage_rate` is in `vals`. Onchange handles UI but server-side `create` with only one of km/rate set will not auto-compute.
- **`action_register_payment` skips payment creation** when all expenses are corporate-card — silently moves state to paid. Audit trail shows "all corporate card — nothing to reimburse" chatter only.
- **PDP audit insert is RAW SQL** — schema drift (e.g. column rename in `pdp.audit_log`) will silently fail (caught + warning logged).
- **Corporate card `payment_mode` enforcement** assumes the field exists (`hasattr(exp, "payment_mode")`); custom builds of `hr_expense` without that field silently no-op.
- **AI image upload encodes via `att.raw` first then base64-decode of `att.datas`** — both code paths re-encode to base64; large attachments use 2x memory at peak.
- **`_default_mileage_rate` catches TypeError/ValueError but not config-param missing** — `get_param` always returns the default string so no exception, but ad-hoc deletion of the param via UI yields `'False'` → `float('False')` → ValueError → falls back to 5000.

## Out of Scope
- **Per-diem allowances / travel advances** — only post-trip claim reimbursement.
- **Multi-currency reimbursement** — payment uses report `currency_id` (defaults to company); cross-currency expenses are converted at AI extraction but `account.payment` is single-currency.
- **Tax invoice (Faktur Pajak) capture on expense** — see `custom_tax_id` / `custom_coretax`.
- **Mileage GPS / route capture** — manual km only.
- **Corporate card statement matching** — see `custom_bank_import` + `custom_accounting_full.custom.reconcile.rule`.
- **Real-time card balance / limit checks** — out of scope; cards are just labels.
