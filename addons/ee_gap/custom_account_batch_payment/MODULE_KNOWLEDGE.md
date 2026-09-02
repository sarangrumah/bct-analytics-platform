---
status: draft
generated_at: 2026-07-24T00:00:00Z
generator: claude-code-handwritten
module: custom_account_batch_payment
manifest_version: 19.0.1.0.0
---

# custom_account_batch_payment

## Purpose
Closes the Enterprise `account_batch_payment` gap for Community: groups posted payments of one bank journal into a batch with a draft→validated→sent→reconciled lifecycle and exports a bank transfer file using pluggable, per-bank Indonesian format records (BCA, Mandiri, BNI, BRI + generic).

## Business Flow
1. From the payments list, the server action `action_payments_create_batch` ("Add to Batch", bound to `account.payment`) calls `action_create_batch_from_selection()` → creates a `custom.account.batch.payment` and opens it. (Or create a batch directly via `menu_custom_batch_payment` → `action_custom_batch_payment`.)
2. `action_validate()`: checks payments exist, are posted (`in_process`/`paid`), share the batch journal and direction; assigns a sequence name; sets state `validated`.
3. `action_generate_export_file()`: renders the transfer file via `export_format_id.render(self)`, stores `export_file`/`export_filename`, sets state `sent`, and calls `mark_as_sent()` on the payments.
4. State auto-advances to `reconciled` (computed) once every payment is `paid` and `is_matched`.
5. `action_draft()` resets to draft (unmarks sent); `action_open_payments()` smart button; `unlink()` only for draft batches.

## Key Models
- `custom.account.batch.payment` (`_name`, `_inherit=["mail.thread"]`) — the batch: one bank journal, lifecycle + export.
- `custom.batch.payment.format` (`_name`) — pluggable export-file layout with per-bank renderers.
- `account.payment` (`_inherit`) — adds batch link + batch-creation action.

No `_auto=False` model.

## Important Fields
- `custom.account.batch.payment`: `name` (Char, readonly, default "New", from sequence), `journal_id` (required, domain bank/cash, check_company, tracked), `company_id`/`currency_id` (related), `date` (required), `batch_type` (`outbound`/`inbound`, default outbound, tracked), `payment_ids` (O2m account.payment via `batch_payment_id`), `payment_count`/`amount_total` (computed), `export_format_id` (M2o format), `export_file` (Binary, attachment), `export_filename` (Char), `state` (draft/validated/sent/reconciled — computed+stored, `readonly=False`, tracked).
- `custom.batch.payment.format`: `name`, `code` (Char, required, indexed, unique `_code_uniq`), `bank_label`, `active`, `encoding` (default utf-8), `delimiter` (default ","), `date_format` (default `%d/%m/%Y`), `file_extension` (default csv), `include_header` (default True), `corporate_id`, `debit_account`, `note` (Text).
- `account.payment`: `batch_payment_id` (M2o custom.account.batch.payment, `index="btree_not_null"`, copy=False).

## Public Methods
- Batch: `action_validate()`, `action_generate_export_file()`, `action_draft()`, `action_open_payments()`, `unlink()` (guarded), `_compute_totals()`, `_compute_state()`.
- Format: `render(batch)` → `(bytes, filename)`; dispatches to `_render_<code>()` or `_render_generic()`; helpers `_validate_batch()`, `_rows()`, `_write_delimited()`; per-bank `_render_bca_mcm`, `_render_mandiri_mcm`, `_render_bni`, `_render_bri`.
- Payment: `action_create_batch_from_selection()` (`@api.model`).

## Integration Points
- **Depends on:** `account` only.
- **Inherits:** `mail.thread` (batch) and `account.payment`. Uses core `mark_as_sent`/`unmark_as_sent`/`is_sent`/`is_matched`/`state` on payments.
- Sequence `custom.account.batch.payment` (prefix `BATCH/%(year)s/%(month)s/`, padding 4, company-independent).
- Seeded formats (`data/batch_export_formats.xml`): `format_bca_mcm` (bca_mcm), `format_mandiri_mcm` (mandiri_mcm), `format_bni` (bni), `format_bri` (bri).
- Server action bound to `account.model_account_payment`. Menus `menu_custom_batch_payment`, `menu_custom_batch_payment_format`. No cron, no `ir.config_parameter`, no `res.config.settings`.

## Gotchas
- `_compute_state`: only the sent→reconciled hop is automatic (all payments `paid` and `is_matched`); other transitions are manual writes. State is stored+computed with `readonly=False`.
- `action_validate` rejects payments not in `in_process`/`paid`, on a different journal, or of mismatched direction; the batch is named on first validate.
- `_validate_batch` (called during export) raises if any payment's `partner_bank_id.acc_number` is missing.
- `render` dispatches by `code` via `getattr`, falling back to `_render_generic`. Amounts: generic/Mandiri/BNI/BRI use `%.2f`; BCA uses `%.0f` (IDR, no decimals). Line terminator `\r\n`.
- **Export layouts are unverified baselines**, not contract-frozen — refine each `_render_*` against real bank-portal sample files before production.
- `unlink` is blocked unless all selected batches are draft.
- `action_create_batch_from_selection` guards: only posted payments, single journal, single direction, none already batched.

## Out of Scope
- No SEPA/pain.001 XML or non-Indonesian formats (only delimited CSV baselines for BCA/Mandiri/BNI/BRI + generic), no upload/transmission to the bank (produces a downloadable file only), no payment creation/posting, and no reconciliation logic of its own (relies on core `is_matched`).
