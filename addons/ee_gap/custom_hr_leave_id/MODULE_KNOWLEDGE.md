---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_hr_leave_id
manifest_version: 19.0.0.2.0
---

# custom_hr_leave_id

## Purpose
Indonesian localization for `hr.leave` / CE `hr_holidays`. Adds the regulatory leave categories (cuti tahunan, cuti melahirkan 6 months per UU Cipta Kerja No. 6/2023, cuti haid, cuti besar, cuti alasan penting, cuti di luar tanggungan), an Indonesian public-holiday master seeded 2024-2026, holiday-overlap warnings on leave requests, a carry-over policy with annual cron stub, auto pro-rated cuti tahunan allocation on employee hire, and a SQL view aggregating leave balances per employee × type × year. Approval flows through `custom_approval_engine` via the `approval.mixin`.

## Business Flow
- HR ensures `hr.leave.type` records carry an `x_id_leave_category` (e.g. `cuti_tahunan`, `cuti_melahirkan`, `cuti_haid`). Seeded by `data/id_leave_types.xml`.
- Public holidays are seeded from `data/id_public_holiday_2024.xml` / `_2025.xml` / `_2026.xml` (noupdate=1) into `id.public.holiday` (date, type_code ∈ national/regional/religious).
- Cron `cron_import_public_holidays` (in `id.public.holiday`) runs to verify current + next year are seeded; only logs warnings when missing (no upstream API fetch implemented).
- New `hr.employee` with `x_auto_leave_allocation=True` (default) triggers `_x_create_initial_annual_allocations` in `create`: for each `hr.leave.type` of category `cuti_tahunan`, pro-rate 12 days from max(hire_date, year-start) to year-end and create an `hr.leave.allocation` (regular type). Already-existing allocation in the year is skipped. Failures are logged but do not block employee creation.
- Employee files an `hr.leave` (Time Off request). The `_compute_x_overlapping_holidays` field finds `id.public.holiday` rows within `[date_from, date_to]` and produces `x_overlapping_holidays_count` + `x_overlapping_holidays_warning` (e.g. "2 public holiday(s) overlap..."). These are computed, non-stored — purely advisory; the module does **not** subtract them from `number_of_days`.
- Approval workflow is delegated to `custom_approval_engine` (via `approval.mixin` injected on `hr.leave`). `action_confirm` (employee submit) is gated by `_approval_request_or_proceed()`: when a leave matrix matches, clicking Confirm auto-submits the approval (Waiting Approval) and does NOT advance to the native To-Approve state until the final tier approves (`_approval_on_granted` re-runs `action_confirm`). With no matrix configured the engine is a no-op and Odoo's native manager approval stands alone. The standalone "Request Approval" button was removed from the leave form.
- Annual carry-over cron `custom.leave.carryover.policy.cron_apply_carryover` runs on Jan 1 (per `data/id_public_holiday_cron.xml`): for each active policy, find previous-year validated allocations, compute `remaining = number_of_days - leaves_taken`, intended carry = `min(remaining, max_carryover_days)`. **Currently a stub — logs intent only, no rewrites performed.**
- `custom.leave.balance.report` is a read-only Postgres view (`_auto = False`) joining `hr_leave_allocation` (state=validate) with `hr_leave` (state=validate) on (employee, leave_type, year-of-date_from) to produce per-row `allocated`, `used`, `remaining`.

## Key Models
- `hr.leave.type` (inherited) — Adds `x_id_leave_category` (Selection of 6 Indonesian regulatory categories).
- `hr.leave` (inherited) — Mixes in `approval.mixin`; adds holiday-overlap compute fields (`x_overlapping_holidays*`); overrides `action_confirm` to auto-submit approval + defines `_approval_on_granted` (re-runs `action_confirm` after grant).
- `hr.employee` (inherited) — Adds `x_auto_leave_allocation` flag and the pro-rated allocation hook.
- `id.public.holiday` — Indonesian public holiday master; `date` indexed, `(date, name)` unique.
- `custom.leave.carryover.policy` — Per leave-type policy (max days, expiry months); unique on `leave_type_id`.
- `custom.leave.balance.report` — `_auto=False` SQL view; per (employee, leave_type, year) → (allocated, used, remaining).

## Important Fields
- `hr.leave.type.x_id_leave_category` (Selection: cuti_tahunan/cuti_melahirkan/cuti_haid/cuti_besar/cuti_alasan_penting/cuti_di_luar_tanggungan) — drives policy lookups; **regulator-aligned values**.
- `hr.leave.x_id_leave_category` (Selection, related, stored) — denormalised for filtering.
- `hr.leave.x_overlapping_holidays` (M2M `id.public.holiday`, computed, **not stored**) — advisory.
- `hr.leave.x_overlapping_holidays_count` / `_warning` (Integer/Char, computed, not stored) — advisory.
- `hr.employee.x_auto_leave_allocation` (Boolean, default True) — toggle for the hire-time hook.
- `id.public.holiday.type_code` (Selection: national/regional/religious, default national).
- `id.public.holiday.year` (Integer, computed from `date`, stored, indexed).
- `custom.leave.carryover.policy.max_carryover_days` (Integer, default 5).
- `custom.leave.carryover.policy.expiry_months_after_year_end` (Integer, default 3 = end of March).
- `custom.leave.balance.report.allocated/used/remaining` (Float, readonly) — SQL-view columns; `remaining = allocated - used` per (employee, leave_type, year) bucket where year = `EXTRACT(YEAR FROM COALESCE(date_from, create_date))`.

## Public Methods
- `id.public.holiday.cron_import_public_holidays()` (`@api.model`) — Log-only verification of seed presence for current + next year.
- `custom.leave.carryover.policy.cron_apply_carryover()` (`@api.model`) — **Stub**; iterates active policies, computes intended carry per allocation, **does not write**.
- `hr.employee._x_create_initial_annual_allocations()` — Pro-rated `cuti_tahunan` allocation for the current year, skips if exists.
- Compute methods (private but BRD-relevant): `_compute_x_overlapping_holidays`, `_compute_year` (on holiday), `_compute_name` (on policy).

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `hr`, `hr_holidays`, `custom_approval_engine`, `mail`.
- **Inherits from:** `hr.leave` (+ `approval.mixin`), `hr.leave.type`, `hr.employee`.
- **Extended by:** payroll (`custom_hr_payroll_id`) consumes employee allocations indirectly for prorated calculations (not direct dependency).
- **External calls:** the `cron_import_public_holidays` cron comments mention upstream APIs (api-harilibur, dayoffapi) as a TODO — **not implemented**.
- **Cross-vertical:** generic Indonesian-localized leave capability.

## Gotchas
- **Overlapping holidays are NOT auto-deducted from leave days.** `x_overlapping_holidays_warning` is purely informational. If a leave spans a public holiday, `number_of_days` is unchanged — operator/employee expected to adjust manually. BRDs assuming "holidays auto-excluded" will fail QA.
- **Carry-over cron is a stub** — it logs `"would carry X days"` but performs no allocation rewrite. Real carry-over must be implemented before relying on `max_carryover_days`/`expiry_months_after_year_end`.
- **Pro-rated allocation formula** — `round(12 × remaining_year_days / total_year_days)` from `max(hire_date, year_start)`. Uses **calendar days**, not working days; the regulator standard for cuti tahunan is "12 hari kerja setelah masa kerja 12 bulan". The hire-time hook ignores the 12-month tenure requirement.
- **`first_contract_date` may not exist** on `hr.employee` (CE) — code uses `getattr(self, "first_contract_date", False)` and falls back to `create_date.date()`. Without `hr_contract` installed, hire date proxy is record creation date.
- **`id.public.holiday` has no company/region scoping** — multi-tenant or multi-region setups (Bali vs national holidays) share the same table.
- **SQL view groups year by `date_from` or `create_date`** — allocations spanning year boundaries land in the year of `date_from`; an allocation `date_from=2024-12-15`/`date_to=2025-12-14` shows entirely in 2024.
- **No company_id filter on the balance report view** — multi-company tenants see all companies' rows.
- **Auto allocation hook swallows all exceptions** (`except Exception:`) — silent failure on employee creation if allocation fails.
- **Cuti melahirkan 6 months (UU Cipta Kerja)** is a category label, not an enforced policy — no special allocation logic or auto-paid-leave rules beyond what `hr.leave.type` itself supports.
- **Cuti haid 2 days/month** is mentioned in module summary but **not implemented** as a recurring auto-allocation; HR must manually allocate.
- **`(date, name)` unique constraint** allows the same date to host multiple holidays only if the names differ — operators must be careful when re-seeding.
- **Public holidays seeded as "static, best-effort"** for 2024-2026; floating Islamic holidays (Eid) may shift in actual SKB; verify each year.

## Out of Scope
- **Live upstream public-holiday API fetch** — listed as TODO; cron only logs missing years.
- **Auto-deduction of public holidays from leave-day counts.**
- **Cuti haid auto-allocation.**
- **Carry-over allocation rewrites** — stub only.
- **Regional/per-province public holidays beyond `type_code='regional'` tagging.**
- **Approval matrix definition** — provided by `custom_approval_engine`; this module only wires the mixin.
- **12-month tenure gate** on cuti tahunan eligibility.
