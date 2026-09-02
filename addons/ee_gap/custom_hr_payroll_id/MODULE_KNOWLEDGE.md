---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_hr_payroll_id
manifest_version: 19.0.0.1.0
---

# custom_hr_payroll_id

## Purpose
Self-contained Indonesian payroll engine — does **not** depend on Odoo EE `hr_payroll`. Implements PPh 21 monthly withholding under the PP 58/2023 TER regime (effective Jan 2024), legacy annualised calculation, annual reconciliation under UU HPP, BPJS Kesehatan + Ketenagakerjaan (JHT/JKK/JKM/JP) contributions, PTKP per PMK 101/2016, THR runs, and SPT 1721 A1 annual reporting (PDF + Coretax XML).

It is the canonical payroll module: any other HR module needing payroll integration (attendance overtime, timesheet OT, lunch deductions) feeds `hr.work.entry` records or extends `hr.payslip` here. On approve, the payslip materialises a draft `custom.coretax.bukti.potong` (Bupot PPh 21) for Coretax submission.

## Business Flow
- Operator pre-fills `hr.employee` Indonesian fields: `x_custom_nik` (16 digits), `x_custom_npwp` (15 or 16 digits), `x_custom_ptkp_status` (TK/0…K/I/3), `x_custom_employment_type` (`pegawai_tetap` / `pegawai_tidak_tetap` / `bukan_pegawai`), `x_custom_bpjs_kesehatan_no`, `x_custom_bpjs_tk_no`. `x_custom_ter_category` is auto-derived from PTKP via `PTKP_TO_TER_CATEGORY`.
- HR officer opens `hr.payslip.batch.wizard`, picks `period_year` + `period_month` (and optional `is_thr`), runs `action_run()` — for each in-scope employee (active in current company, or explicit list) the wizard creates a `hr.payslip` (state `draft`) and calls `slip.action_compute()`.
- `_do_compute(config)` computes BPJS + PPh21 and writes lines into `hr.payslip.line`, transitioning state `draft`→`computed`. PPh 21 branches:
  - **THR run** (`is_thr=True`): treats THR as monthly gross, taxable_year = max(0, gross − PTKP), then full progressive UU HPP. Method tag = `annual_recon`.
  - **TER** (when `calc_method='ter'` AND `employment_type='pegawai_tetap'` AND month ≠ 12): looks up `hr.payroll.ter.bracket.get_rate(ter_cat, gross_total_month)` (returns fraction), `pph_month = gross_total_month * rate`. Method = `ter`.
  - **Annualised fallback** (December always, or non-TER configs): annual_gross = monthly × 12; biaya jabatan = min(5% × annual_gross, 6,000,000); net_year = annual_gross − biaya_jabatan − jht_emp×12 − jp_emp×12 − PTKP; PPh year via `_compute_pph21`; pph_month = pph_year / 12. Method = `annualised` or `annual_recon` for December.
- `action_approve()` moves `computed`→`approved`, calls `_materialise_bupot_pph21()` which creates one `custom.coretax.bukti.potong` per slip (idempotent on `bupot_id`) with `jenis_pph='pph_21'`, `dpp=gross+tj+tl`, `pph_terpotong=pph21`, `tarif=ter_rate_used`, state `draft`. Failure is logged but does not block approval.
- `action_pay()` moves `approved`→`paid`. `action_draft()` reverts to `draft`. `write()` blocks edits to `gross_salary`/`tunjangan_*` once `approved` or `paid`.
- Every state-change writes a row into `pdp.audit_log` via raw SQL (classification `financial`) including actor, tenant_db, slip name, state, THP, PPh 21.
- Year-end: HR officer opens `hr.payroll.spt.a1.wizard`, picks `fiscal_year` (default = current year - 1), optionally selects employees, picks `output_format` (`pdf`/`xml`/`both`). `action_run()` aggregates all `approved`/`paid` slips of the year, recomputes annual progressive PPh 21, compares to sum of monthly deductions to surface `delta` (kurang/lebih bayar), and emits the PT.A1 PDF and/or the `SPT_1721_A1_<year>.xml` Coretax batch as an `ir.attachment`.
- A `pre_init_hook` runs before install (purpose: seed/migration; not detailed here).

## Key Models
- `hr.payslip` — One row per employee × period × THR flag. Holds gross, BPJS amounts, PPh 21, THP, method used, Bupot link, state. `_inherit = ['mail.thread', 'mail.activity.mixin']`. NOT inheriting CE `hr.payroll`'s payslip — this is a fresh `_name = "hr.payslip"`.
- `hr.payslip.line` — Per-payslip breakdown row (sequence, code, label, type ∈ {income, deduction, info}, amount).
- `hr.payroll.ter.bracket` — TER table row (category A/B/C × lower_bound × upper_bound × rate%). `upper_bound=0` means open-ended. Seeded via `data/hr_payroll_ter_data.xml`.
- `hr.payroll.config` — Singleton-style configuration (default record auto-created by `get_default()`). Holds calc_method, PTKP values, biaya jabatan, all BPJS percentages + ceilings.
- `hr.employee` (inherited) — Adds 8 Indonesian payroll fields prefixed `x_custom_*`.
- `hr.payslip.batch.wizard` (TransientModel) — Bulk payslip generator with `skip_if_exists` + `auto_approve`.
- `hr.payroll.spt.a1.wizard` (TransientModel) — Annual SPT 1721 A1 generator (PDF + XML batch).

## Important Fields
- `hr.payslip.state` (Selection: draft/computed/approved/paid) — drives lifecycle; financial fields locked once approved.
- `hr.payslip.is_thr` (Boolean) — distinguishes THR runs (unique constraint on `(employee_id, period_year, period_month, is_thr)`); routes to a different PPh 21 branch.
- `hr.payslip.calc_method_used` (Selection: ter/annualised/annual_recon, readonly) — cached method tag for audit.
- `hr.payslip.ter_category_used` / `ter_rate_used` (Selection A/B/C, Float %) — TER applied at compute time, stored on the slip.
- `hr.payslip.pph21` (Monetary, readonly, tracked) — monthly PPh 21 to be withheld.
- `hr.payslip.bpjs_kesehatan_emp` / `bpjs_kesehatan_company` (Monetary, readonly) — Kesehatan contributions, computed off `min(gross, bpjs_kesehatan_ceiling=12,000,000)` × 1% (emp) / 4% (co).
- `hr.payslip.bpjs_jht_emp` / `bpjs_jht_company` (Monetary, readonly) — JHT 2% (emp) / 3.7% (co) of gross_total_month, **no ceiling**.
- `hr.payslip.bpjs_jp_emp` / `bpjs_jp_company` (Monetary, readonly) — JP 1% (emp) / 2% (co) of `min(gross, bpjs_jp_ceiling=10,042,300)`.
- `hr.payslip.bpjs_jkk` / `bpjs_jkm` (Monetary, readonly) — Company-only JKK (default 0.54%, range 0.24–1.74% per industry) and JKM (0.30%) on gross_total_month.
- `hr.payslip.take_home_pay` (Monetary, readonly, tracked) — `gross_total_month − (bpjs_kes_emp + bpjs_jht_emp + bpjs_jp_emp + pph_month)`. **JKK/JKM/Kesehatan-company/JHT-company/JP-company are NOT deducted from THP** (employer-borne).
- `hr.payslip.bupot_id` (M2o `custom.coretax.bukti.potong`, readonly, no copy) — materialised on approve; idempotent.
- `hr.employee.x_custom_ptkp_status` (Selection 12 values) — drives PTKP amount via `config.get_ptkp()` and TER category via `_compute_ter_category`.
- `hr.employee.x_custom_ter_category` (Selection A/B/C, stored compute) — read by payslip compute; A=TK/0/1+K/0, B=TK/2/3+K/1/2, C=K/3+K/I/*.
- `hr.payroll.config.calc_method` (Selection: ter/annualised) — TER is default since Jan 2024; December always reconciles via annual progressive regardless.
- `hr.payroll.config.ptkp_*` (12 Float fields) — PTKP per PMK 101/2016 (TK/0=54M up to K/I/3=126M).
- `hr.payroll.ter.bracket.upper_bound` (Float) — `0` is a sentinel for "open-ended highest bracket"; the comparison is `monthly_gross <= upper_bound`.

## Public Methods
- `hr.payslip.action_compute()` — Compute BPJS + PPh21, write lines, draft→computed, audit `compute`.
- `hr.payslip.action_approve()` — computed→approved, materialise Bupot, audit `approve`.
- `hr.payslip.action_pay()` — approved→paid, audit `pay`.
- `hr.payslip.action_draft()` — Force back to draft.
- `hr.payslip._materialise_bupot_pph21()` — Create the draft Coretax Bupot if PPh21 > 0 and no Bupot yet; silent failure mode (chatter message + log).
- `hr.payslip._pdp_audit(action_label)` — Raw INSERT into `pdp.audit_log` (`classification='financial'`).
- `hr.payroll.config.get_default()` (`@api.model`) — Returns active config or auto-creates one.
- `hr.payroll.config.get_ptkp(status)` — PTKP amount for status code (falls back to TK/0).
- `hr.payroll.ter.bracket.get_rate(category, monthly_gross)` (`@api.model`) — Returns rate **as a fraction** (e.g. 0.05). Open-ended bracket signalled by `upper_bound=0`.
- `hr.payroll.ter.bracket.category_for_ptkp(ptkp_status)` (`@api.model`) — Map PTKP → TER category (A/B/C, default A).
- `_compute_pph21(taxable_year)` (module-level) — Apply progressive brackets `[(60M,5%), (250M,15%), (500M,25%), (5B,30%), (None,35%)]`.
- `hr.payslip.batch.wizard.action_run()` — Bulk-generate + compute (+ optional auto-approve).
- `hr.payroll.spt.a1.wizard.action_run()` — Aggregate yearly slips, recompute annual PPh, emit PDF/XML.
- `hr.payroll.spt.a1.wizard._compute_employee_annual(emp, config)` — Returns per-employee dict (bruto_year, biaya_jabatan, jht_emp, jp_emp, ptkp, taxable_year, pph_due, pph_paid, delta).

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_pdp_taxonomy`, `custom_coretax`, `hr`, `mail`.
- **Inherits from:** `hr.employee` (adds Indonesian payroll fields). `hr.payslip` is a **fresh model** (`_name="hr.payslip"`) — there is no CE `hr.payroll` to inherit since it's an EE-only module; this module replaces that role.
- **Extended by:** `custom_attendance` (creates `hr.work.entry` overtime entries feeding payroll), `custom_timesheet` (same OT bridge), `custom_lunch` (cron aggregates lunch into payslip lines via `x_payslip_id`).
- **External calls:** none direct; `custom_coretax` handles the actual Coretax submission.
- **Cross-vertical:** generic Indonesian payroll capability; required by any tenant employing salaried staff in Indonesia.
- **Audit:** raw `INSERT INTO pdp.audit_log` (classification `financial`) on every compute/approve/pay.

## Gotchas
- **TER table is data-driven** — `hr.payroll.ter.bracket` rates come from `data/hr_payroll_ter_data.xml`. If the data file is missing or wrong, `get_rate()` returns 0.0 and PPh21 will silently be zero for TER cases. Verify seed before go-live.
- **`upper_bound=0` is a sentinel for open-ended**, not "zero". Editing a TER row to set upper_bound=0 turns it into the catch-all top bracket.
- **December always uses annual progressive**, regardless of `calc_method='ter'` — this is the year-end reconciliation. THR runs also use progressive (`annual_recon` tag).
- **THR PPh calculation is simplified** (MVP): it treats the THR amount as one-month gross with progressive brackets applied to `(THR − PTKP)`, not the proper "PPh on bonus = PPh(salary+bonus annualised) − PPh(salary annualised)" formula. This will under/over-tax for high earners.
- **JHT has no ceiling** — the regulation actually had a Rp 8.94M ceiling historically; current implementation applies 2%/3.7% on full gross. Verify before payroll go-live.
- **JKK default is 0.54%** but the industry-correct rate ranges **0.24% to 1.74%** (Tingkat I to V per PP 44/2015). Operator must override `bpjs_jkk_company_pct` on `hr.payroll.config` per company industry classification.
- **`gross_total_month` for BPJS** uses gross + tunjangan_jabatan + tunjangan_lain. BPJS should typically use a "upah" definition (basic + fixed allowances) — verify which tunjangan should be in scope per company SK.
- **Bupot creation fallback** — if employee has no `user_partner_id` and no `work_contact_id`, a **fresh `res.partner` is created with the employee name and `is_company=False`**. This may pollute partners; the operator is expected to "later replace" but nothing enforces it.
- **`no_bupot` is `DRAFT-PPH21-<year><month>-<employee_id>`** — placeholder pending real Coretax-issued number; not a real DJP-format sequence.
- **NPWP validation accepts 15 or 16 digits** stripped of separators; no checksum validation.
- **No multi-company isolation enforced on `hr.payroll.ter.bracket`** — global table; if different companies need different rates, the model needs `company_id` added.
- **`pdp.audit_log` raw INSERT** assumes the `pdp` schema and table exist (provided by `custom_pdp_taxonomy`). Failures are logged but never block payroll workflow.
- **`_pdp_audit_write` (the mixin method) is NOT used here**; the module uses its own `_pdp_audit(action_label)` doing raw SQL. The classification is hardcoded `'financial'`.
- **The SPT 1721 A1 XML schema** (`<SPT1721A1Batch>` → `<Pegawai>` children) is **simplified DJP-style**, not the official Coretax XSD. Real submissions need post-processing.

## Out of Scope
- **EE `hr.payroll` salary-rule engine** — this module replaces it with hard-coded compute methods. No `hr.salary.rule`, no `hr.contract`-driven inputs.
- **Multi-currency payroll** — currency is taken from the company; no FX between gross_salary and contribution currencies.
- **Allowance/deduction master data** — `tunjangan_jabatan` / `tunjangan_lain` are single Monetary fields, not configurable allowance types.
- **Bank file (PMI / bank-specific batch payment file) generation** — `take_home_pay` is computed, not exported to a bank format.
- **Loan/koperasi deduction structures** — no loan amortisation logic.
- **Multi-company TER tables** — the TER bracket has no `company_id`.
- **Variable JKK per employee** — `bpjs_jkk_company_pct` is single-company, not per-job-grade or per-risk-class within the same legal entity.
