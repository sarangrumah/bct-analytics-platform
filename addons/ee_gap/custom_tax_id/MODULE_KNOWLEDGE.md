---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_tax_id
manifest_version: 19.0.0.1.0
---

# custom_tax_id

## Purpose
Indonesian-specific tax engine: PPh withholding (Pasal 23, 4(2), 26, 22, 21) on vendor bills, PPN DPP Nilai Lain (PMK 131/2024) on the tax base, NPWP / NIK validation on partners, and the Faktur Pengganti relink workflow. Sits between Odoo `account` and `custom_coretax` — generates draft `custom.coretax.bukti.potong` records that Coretax then serialises to the DJP e-Bupot XML.

This is the canonical Indonesian withholding + DPP module. Any BRD with "potong PPh", "bukti potong", "PPh 23/4(2)/26", "DPP nilai lain", "PMK 131", "NPWP validation", or "Faktur Pengganti" maps here. Coretax depends on this module's bupot draft for export.

## Business Flow
- **NPWP/NIK setup**: `res.partner.x_custom_npwp` (15 legacy or 16-digit NIK-based since 2024), `x_custom_nik` (16-digit, individuals); computed `x_custom_npwp_status` ∈ valid/invalid/none and `x_custom_has_valid_npwp`. `x_custom_pkp` flag for PPN registration. `x_custom_foreign_counterparty` auto-set when partner country ≠ company country.
- **Withholding catalogue** (`tax.withholding.category`): jenis penghasilan with `pph_kind` ∈ pph_23/pph_4_2/pph_26/pph_22/pph_21, `bupot_object_code` (matches Coretax e-Bupot XML).
- **Withholding rule** (`tax.withholding.rule`): `category_id` + `tarif` + optional `tarif_no_npwp` (PPh 23: 2% → 4% bump) + `account_id` (account hutang pajak) + optional filters `product_category_ids` / `partner_category_ids` / `foreign_only`. Resolution priority `priority desc, sequence asc`.
- **Apply on vendor bill post**: `account.move._post` (for `in_invoice`/`in_refund`) calls `_custom_apply_withholding` BEFORE super. Idempotent (skips if `x_custom_withholding_line_ids` already populated). For each non-display invoice line, resolves rule via `tax.withholding.rule._resolve_for_line(ml)` (filters by `active`, `company_id`, then `foreign_only`/`product_category_ids`/`partner_category_ids`); picks `_effective_tarif(partner)` (`tarif_no_npwp` if vendor lacks valid NPWP). Creates `account.move.withholding.line` with `base = ml.price_subtotal`, `tax = round(base * tarif/100, 2)`. PDP audit row written.
- **Bupot draft materialisation**: `account.move.withholding.line.create()` runs `_materialise_bupot()` which creates a `custom.coretax.bukti.potong` with `no_bupot = "DRAFT-{move.name}-{line.id}"`, `jenis_pph`, `tarif`, `dpp`, `pph_terpotong`, `tanggal_bupot`, `period_year`/`period_month` from invoice_date, `source='outgoing'` (we cut, vendor receives), state=draft. NSFP is empty — Coretax fills after DJP approval.
- **PPN DPP Nilai Lain**: `account.tax.x_custom_dpp_method` ∈ regular/nilai_lain + `x_custom_dpp_factor` (e.g. 11/12 ≈ 0.916667) + `x_custom_dpp_category` enumerating PMK 131/2024 categories. `_dpp_adjust(raw_base)` multiplies by factor when method is nilai_lain. Overridden into Odoo 19's tax pipeline: `_eval_tax_amount_price_excluded`, `_eval_tax_amount_price_included`, `_eval_tax_amount_fixed_amount`.
- **Faktur Pengganti wizard**: applies kode status `01`/`02`/... sequentially on `account.move.coretax_status` with NSFP relinking.
- **Bulk validation wizard**: pre-flight check before Coretax export — NPWP (15/16 digit), NIK (16 digit), DPP > 0, sertel attached + not expired, across a batch of moves.

## Key Models
- `res.partner` (inherited) — NPWP/NIK + PKP + foreign-counterparty flags.
- `tax.withholding.category` — jenis penghasilan + pph_kind + Coretax `bupot_object_code`.
- `tax.withholding.rule` — tarif + account hutang pajak + filters; resolution helper `_resolve_for_line`.
- `account.move.withholding.line` — one row per (vendor bill line × rule); back-refs `bupot_id` to the auto-materialised `custom.coretax.bukti.potong`.
- `account.move` (inherited) — Adds `x_custom_withholding_line_ids` + computed `x_custom_total_withheld`; overrides `_post` to apply withholding.
- `account.tax` (inherited) — DPP Nilai Lain fields + tax-pipeline overrides.
- `product.template` (inherited) — `x_custom_withholding_category_id` hint.
- `tax.faktur.pengganti.wizard` (TransientModel) — kode status sequencing + NSFP relinking.
- `tax.bulk.validation.wizard` (TransientModel) — pre-export validator.

## Important Fields
- `res.partner.x_custom_npwp` (Char) — accepts dots/hyphens; computed status strips them before regex match.
- `res.partner.x_custom_npwp_status` (Selection valid/invalid/none, computed, stored).
- `res.partner.x_custom_has_valid_npwp` (Boolean, computed, stored) — drives `_effective_tarif`.
- `res.partner.x_custom_pkp` (Boolean) — fiscal-position trigger.
- `res.partner.x_custom_foreign_counterparty` (Boolean, computed, stored) — auto from country comparison.
- `res.partner.x_custom_nik` (Char) — 16-digit constraint `_check_nik`.
- `tax.withholding.category.pph_kind` (Selection pph_23/pph_4_2/pph_26/pph_22/pph_21).
- `tax.withholding.category.bupot_object_code` (Char) — kode objek pajak per PER-04/PJ/2023; surfaces in Coretax XML.
- `tax.withholding.rule.tarif` (Float, digits=(6,4), 0–100) — base rate.
- `tax.withholding.rule.tarif_no_npwp` (Float) — bumped rate; 0 = no bump (fall back to `tarif`).
- `tax.withholding.rule.account_id` (M2o `account.account`, liability_current) — required before `active=True` via `_check_account_when_active`.
- `tax.withholding.rule.foreign_only` (Boolean) — switches PPh 23 → PPh 26 routing.
- `tax.withholding.rule.priority` (Integer, default 10) — `priority desc, sequence asc` resolution.
- `account.move.withholding.line.base_amount` / `tax_amount` (Monetary) / `tarif` (Float, 6,4).
- `account.move.withholding.line.bupot_id` (M2o `custom.coretax.bukti.potong`, readonly) — auto-materialised draft.
- `account.move.x_custom_total_withheld` (Monetary, computed, stored) — `sum(withholding_line_ids.tax_amount)`.
- `account.tax.x_custom_dpp_method` (Selection regular/nilai_lain).
- `account.tax.x_custom_dpp_factor` (Float, digits=(12,6), default 1.0) — `_check_dpp_factor` requires > 0 when method = nilai_lain.
- `account.tax.x_custom_dpp_category` (Selection) — 13 enumerated PMK 131/2024 categories (impor/film/emas_perhiasan/kendaraan_bekas/paket_wisata/agen_perjalanan/jasa_pengiriman/hasil_tembakau/pemasaran_perdagangan/freight_forwarding/jasa_lain/ppn_efektif_11_12/ppn_efektif_12).
- `product.template.x_custom_withholding_category_id` (M2o `tax.withholding.category`) — default for jasa konsultan / sewa / royalti.

## Public Methods
- `tax.withholding.rule._resolve_for_line(move_line)` (`@api.model`) — best-matching active rule.
- `tax.withholding.rule._effective_tarif(vendor)` — NPWP-aware rate.
- `account.move._post(soft=True)` — overridden to run `_custom_apply_withholding` before super.
- `account.move._custom_apply_withholding()` — idempotent rule application.
- `account.move.withholding.line._materialise_bupot()` — creates draft `custom.coretax.bukti.potong`.
- `account.tax._dpp_adjust(raw_base)` — multiplies base by factor when nilai_lain.
- `account.tax._eval_tax_amount_price_excluded/_price_included/_fixed_amount` — Odoo 19 tax pipeline hooks.
- Wizards: `tax.faktur.pengganti.wizard.action_apply()`, `tax.bulk.validation.wizard.action_validate()`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_taxonomy`, `custom_pdp_audit`, `custom_coretax`, `custom_accounting_full`, `account`, `purchase`, `product`.
- **Inherits from:** `account.move` (+ `pdp.audited.mixin` from `custom_accounting_full`), `account.tax`, `res.partner`, `product.template`.
- **Extended by:** `custom_coretax_bupot` consumes the draft bupots produced here for e-Bupot XML serialisation.
- **External calls:** none directly (Pajakku/Coretax HTTP calls live in `custom_coretax_pajakku`).
- **Cross-vertical:** Indonesia-locked — DJP-specific rules.

## Gotchas
- **`_post` ordering**: `_custom_apply_withholding` runs BEFORE `super()._post`. The withholding lines are created but the JOURNAL ITEMS for the hutang pajak are NOT created (`account_id` on the rule is captured but no `account.move.line` is debited/credited). The bupot draft is the only persistence. Module description says "balancing journal items" but code currently only materialises bupot.
- **Idempotency via line presence** — if `x_custom_withholding_line_ids` exist, the engine skips. Re-posting a move that lost its lines (e.g. through `unlink`) will NOT re-apply.
- **`_resolve_for_line` ignores `pph_22`/`pph_21` filters** — there's no special-casing; PPh 21 should be handled by a payroll module, not vendor bills.
- **Bupot `no_bupot = "DRAFT-{move.name}-{line.id}"`** is a placeholder — DJP NSFP arrives via Coretax export. Duplicate detection on this field requires Coretax overwrites it.
- **DPP factor applied before `super()`** in tax pipeline — works for the standard pricing branches but custom child taxes that override the same method will need to chain `super()._dpp_adjust(...)` correctly.
- **`account.tax._compute_amount` legacy hook is NOT overridden** — only the Odoo 19 `_eval_tax_amount_*` methods. Backports to 16/17 will not pick up DPP NL.
- **`foreign_only` flag uses `partner.country_id != company.country_id`** — if either country is unset, treated as not foreign (no PPh 26).
- **NPWP regex strips dots/hyphens** — formatted display like `01.234.567.8-901.000` is treated as valid; raw `01234567890100` (14 digits) is invalid.
- **`tarif_no_npwp=0` is a sentinel** for "no bump" — explicitly setting 0 does NOT mean "0% tarif when no NPWP"; use `0.0001` or similar for true zero.
- **`x_custom_pkp` is a flag without behavior in-tree** — fiscal-position automation is left for Coretax / verticals.
- **Bupot creation failure does NOT block withholding line creation** — error posted to chatter, line stays.

## Out of Scope
- **PPh 21 personal payroll withholding** — see payroll modules (separate from this AP-focused engine).
- **e-Bupot XML serialisation** — `custom_coretax` / `custom_coretax_bupot` own the actual XML build + DJP submission.
- **PPN output tax compute** — Odoo's standard tax engine handles output PPN; this module only adjusts the BASE via DPP NL.
- **Pajakku API calls** — see `custom_coretax_pajakku`.
- **Faktur Masukan (PPN input) workflow** — only DPP and PPh withholding on vendor side; PPN input fiscal positions live in `custom_coretax`.
- **NPWP validation against DJP API** — only regex shape validation; live status check is not implemented.
