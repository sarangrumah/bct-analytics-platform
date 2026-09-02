---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_pph_witholding
manifest_version: 19.0.0.2.0
---

# custom_pph_witholding

## Purpose
Generic, reusable **Indonesian PPh withholding engine** (PPh 21 / 22 / 23 / 26 / 4(2) / 15) extracted from `era_ppob_commission` and generalised. Provides three pieces:

1. A **rate registry** (`custom.witholding.rate`) keyed by `(pph_type, service_category, effective_date)` with separate **with-NPWP** and **without-NPWP** rates (latter is typically punitive 2× per UU PPh).
2. A stateless **engine** (`custom.witholding.engine`, AbstractModel) with `compute(partner, amount, pph_type, date, service_category)` and `compute_and_log(...)` producing a `custom.witholding.application` log entry.
3. An **append-only application log** (`custom.witholding.application`) auditing every computation, optionally linked to a `custom.bupot.unifikasi.line` for downstream Coretax reporting.

Triggers: manual `custom.apply.witholding.wizard` on any `account.move`; lazy `action_post` hook on `account.payment` (vendor payments with negative-amount tax lines); lazy override on `hr.payslip._custom_pph_apply_pph21` (no-op if `hr.payslip` not installed).

## Business Flow
- Tax officer maintains `custom.witholding.rate` rows: per `(pph_type, service_category, effective_date_from..to)` set `with_npwp_rate` and `without_npwp_rate`, optional `legal_basis` citation.
- `custom.witholding.rate._find_active(pph_type, service_category, date)` picks the most-specific active row (matching `service_category` first), falls back to `service_category = "general"` if none.
- `custom.witholding.engine.compute(partner, amount, pph_type, date, service_category)` — `_has_valid_npwp(partner)` checks `res.partner.vat` after stripping `.`/`-`/space against `^\d{15,16}$`. Returns `{rate, withheld, gross_remain, applicable_rule_id, has_npwp}`. `withheld` is integer rupiah (Decimal `ROUND_HALF_UP`); zero if no rule matched.
- `engine.compute_and_log(...)` does the same plus creates a `custom.witholding.application` row (`state="computed"` by default, callable with `state="applied"`).
- Manual flow: user opens an `account.move` and clicks "Apply Witholding" → `account.move.action_open_witholding_wizard` opens `custom.apply.witholding.wizard` prefilled with partner + `amount_untaxed or amount_total`. User runs `action_preview` (no log) then `action_apply` (log with `state="applied"`); raises `UserError` if no rate matched. Returns an `act_window` opening the resulting application record.
- Payment hook: `account.payment.action_post()` → `_custom_pph_log_witholding()` iterates outbound payments, fetches `reconciled_bill_ids`, detects "withholding tax was applied" via any `line.tax_line_id.amount < 0` on the bill, and logs a PPh23 application (`amount=bill.amount_untaxed`, `date=payment.date`, `source_doc=payment`, `state="applied"`). Failures are swallowed (`_logger.warning`).
- Payslip hook: `hr.payslip._custom_pph_apply_pph21()` logs a PPh21 application per slip (`amount=slip.basic_wage`, `date=slip.date_to`); only attached when `hr.payslip` is in the registry. Failures logged, never raised.
- Application records expose `action_mark_applied` (computed → applied) and `action_reverse` (free → reversed).

## Key Models
- `custom.witholding.rate` — Rate matrix; inherits `pdp.audited.mixin`.
- `custom.witholding.engine` (AbstractModel) — Stateless service.
- `custom.witholding.application` — Per-event log; inherits `pdp.audited.mixin`, `mail.thread`, `mail.activity.mixin`.
- `custom.apply.witholding.wizard` (TransientModel) — Manual application UI.
- `account.move` (inherited) — adds `action_open_witholding_wizard` button.
- `account.payment` (inherited) — overrides `action_post` to log withholdings.
- `hr.payslip` (lazily inherited) — adds `_custom_pph_apply_pph21`.

## Important Fields
- `custom.witholding.rate.pph_type` (Selection: 23/22/4_2/15/21/26, required) — note the full PPh21 inclusion (unlike `custom_coretax_bupot`).
- `custom.witholding.rate.service_category` (Char, required, default `"general"`) — discriminator e.g. `sewa`, `jasa_teknik`, `manajemen`.
- `custom.witholding.rate.with_npwp_rate` / `without_npwp_rate` (Float 6,4, required, 0..100) — both required; "without NPWP" is typically 2× the with-NPWP rate per UU PPh Pasal 23 ayat (1a).
- `custom.witholding.rate.effective_date_from` (Date, required) / `effective_date_to` (Date, optional open-ended) — temporal validity.
- `custom.witholding.rate.legal_basis` (Text) — citation for auditors.
- `custom.witholding.application.partner_id` (M2o `res.partner`, indexed) — cuttee; sourced from caller (may be empty for bulk computes).
- `custom.witholding.application.source_doc` (Reference: account.move / account.payment / hr.payslip) — back-link.
- `custom.witholding.application.pph_type` (Selection incl. PPh21) — copied from the engine call.
- `custom.witholding.application.gross` / `rate` / `withheld` (Float) — engine results.
- `custom.witholding.application.has_npwp` (Boolean) — captured at compute time so retro-changes to partner.vat don't rewrite history.
- `custom.witholding.application.rule_id` (M2o `custom.witholding.rate`, `ondelete="restrict"`) — rate row that fired; restricts deletion of rules with applications.
- `custom.witholding.application.bupot_line_id` (M2o `custom.bupot.unifikasi.line`, `ondelete="set null"`) — link to the bupot line produced from this application.
- `custom.witholding.application.state` (Selection: computed/applied/reversed, tracked).

## Public Methods
- `custom.witholding.engine.compute(partner, amount, pph_type, date=None, service_category=None)` (`@api.model`) — pure compute; returns dict.
- `custom.witholding.engine.compute_and_log(partner, amount, pph_type, ..., state="computed")` (`@api.model`) — compute + persist application row; returns dict with extra `application_id`.
- `custom.witholding.rate._find_active(pph_type, service_category, date)` (`@api.model`) — fallback to `general` if specific category misses.
- `custom.witholding.application.action_mark_applied()` / `action_reverse()`.
- `custom.apply.witholding.wizard.action_preview()` / `action_apply()`.
- `account.move.action_open_witholding_wizard()` — opens wizard prefilled with move context.
- `account.payment.action_post()` (overridden) — calls `_custom_pph_log_witholding()` after super.
- `hr.payslip._custom_pph_apply_pph21()` — best-effort PPh21 logger; only present if `hr.payslip` model exists at import time.
- Module-level helpers: `_has_valid_npwp(partner)`, `_round_half_up_int(value)`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_coretax`, `account`, `mail`.
- **Inherits from:** `account.move`, `account.payment`, `hr.payslip` (lazy), `pdp.audited.mixin` (on rate + application), `mail.thread`/`mail.activity.mixin` (on application).
- **Extended by:** `custom_pdp_masking` declares this as a dependency (so masking rules can target the application log), no direct subclass.
- **External calls:** none.
- **Cross-vertical:** generic Indonesian tax compliance.

## Gotchas
- **No automatic write-back to `account.move`** — the wizard logs an application and opens the application form, but it does NOT add a tax line to the bill / reduce the payment amount. Integrating with Odoo's `account.tax` model is left to downstream code; this module just records what *would have been* withheld.
- **`account.payment` hook detects withholding heuristically** by checking for any `line.tax_line_id.amount < 0` on the reconciled bill. False positives (any negative tax) trigger a PPh23 log; false negatives (withholding modelled differently) miss entirely.
- **`hr.payslip._custom_pph_apply_pph21` is NEVER auto-invoked** — it must be called explicitly from a payslip workflow override in another module. Out of the box it is dead code.
- **`compute_and_log` always creates a row, even when no rule matched** — `rule_id=False`, `withheld=0`, `rate=0`. Watch for log noise.
- **PPh21 is fully supported by the engine but `custom_coretax_bupot` excludes it from its `pph_type` selection**, so an `application.bupot_line_id` link is impossible for PPh21 here. PPh21 reporting routes through `custom_coretax`'s bupot21 XML export.
- **`_find_active` ordering is `effective_date_from desc, limit=1`** — if two rules with the same `effective_date_from` exist for the same `(pph_type, service_category)`, the one with the higher id wins (database-dependent tiebreak); no warning.
- **NPWP validation strips `.`/`-`/space** but does NOT strip other Unicode whitespace or zero-width characters; pasted values from PDF may falsely fail.
- **`_round_half_up_int` rounds to whole rupiah** — fractional withholding is not preserved.
- **Reverse is a free state transition** with no constraint that the application be in `applied` first; auditors may need additional guardrails.
- **This module's groups sit on its own `custom_pph_witholding.res_groups_privilege_witholding` privilege** ("PPh Withholding") — Odoo 19 renders every group sharing one `res.groups.privilege` as a single pick-one dropdown on the user form, so a privilege shared across modules makes saving a user silently drop the other modules' groups. The privilege carries `custom_core.module_category_custom_platform`, so the selector still appears alongside the other custom modules. Do not point new groups at `custom_core.res_groups_privilege_custom_platform`.

## Out of Scope
- **Posting withholding tax lines into Odoo accounting** — the engine computes amounts; integrating them into `account.move.tax_line_id` or splitting payments is the consumer's responsibility.
- **Reversal accounting entries** — `action_reverse` only flips state; no GL impact.
- **Multi-step withholding (gross-up calculations)** — the engine is single-rule single-pass.
- **PPh21 progressive bracket calculation** — `_find_active` returns one rate row; progressive PPh21 needs a richer compute path (e.g. annual PTKP/bracket logic) that lives in HR/payroll, not here.
- **Withholding certificates (Bukti Potong PDF)** — see `custom_coretax_bupot` for unifikasi or `custom_coretax` for PPh21 doc types.
- **Foreign-currency rounding rules** — IDR only; no FX conversion.
