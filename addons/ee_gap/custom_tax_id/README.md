# Custom Tax Indonesia

Indonesian withholding-tax engine + PPN DPP Nilai Lain + Faktur
Pengganti workflow. Sits between `account` and `custom_coretax`.

## PPh Withholding

### Models

- `tax.withholding.category` — catalogue of jenis penghasilan (PPh 23 /
  4(2) / 26 / 22 / 21). Seeded with 18 categories + Bupot object codes.
- `tax.withholding.rule` — declarative rule mapping (category × filters)
  to (tarif, tarif_no_npwp, hutang account). Resolution priority:
  product category > partner category > foreign-only > generic.
- `account.move.withholding.line` — one per matched (bill line × rule).
  Auto-materialises a draft `custom.coretax.bukti.potong`.

### Auto-detection

On `account.move._post()` for `in_invoice` / `in_refund` moves:

1. For each non-display invoice line, resolve the best matching rule.
2. Compute `tax_amount = base * effective_tarif`.
3. `effective_tarif` switches to `tarif_no_npwp` if vendor lacks valid NPWP.
4. Foreign-counterparty rules (`foreign_only`) trigger only when the
   vendor's country differs from the company's country — used for PPh 26.
5. Withholding line created + Bukti Potong draft materialised + audit
   logged.

Idempotent — re-running on a posted move with existing withholding lines
is a no-op.

### Default Rules (Inactive by Default)

10 seeded rules covering the common cases (jasa konsultan/teknik/
manajemen/lain, sewa harta, royalti, bunga, sewa tanah/bangunan final,
PPh 26 jasa/royalti). All ship `active=False` — operators activate them
after binding `account_id` to the company's hutang-pajak ledger.

## PPN DPP Nilai Lain (PMK 131/2024)

`account.tax` extended with:

- `x_custom_dpp_method`: `regular` | `nilai_lain`
- `x_custom_dpp_factor`: multiplier applied to `base_amount` before
  rate computation (e.g. `11/12 ≈ 0.916667` for the 11%-effective-via-12%
  transition).
- `x_custom_dpp_category`: enumerated PMK 131/2024 category for XML
  reporting downstream in Coretax.

Implementation overrides `account.tax._compute_amount` to apply the
factor — works seamlessly on invoice line tax computation, period
reports, and Faktur Pajak XML serialisation.

Reference factors (PMK 131/2024) stored as `ir.config_parameter` for
operator copy-paste:

| Category | Factor |
|---|---|
| PPN 11% effective via DPP 11/12 (transitional 2025) | 0.916667 |
| Paket wisata, agen perjalanan, jasa pengiriman, freight, film, kendaraan bekas, pemasaran perdagangan | 0.10 |
| Emas perhiasan | 0.20 |
| Impor BKP | 1.0 (DPP = nilai impor + bea masuk + cukai) |

## Faktur Pengganti

Per PER-11/PJ/2025, NSFP's 2-digit kode status indicates whether the
Faktur is the original (`00`) or a replacement (`01`-`09`). Workflow:

1. Operator runs `tax.faktur.pengganti.wizard`, picks source Faktur,
   types reason.
2. Wizard creates a new `account.move` copying the source with
   `x_custom_coretax_kode_status` bumped (`00→01`, `01→02`, ..., max `09`).
3. Source move's NSFP is cleared (logically void), `coretax_status`
   marked as superseded.
4. Audit row written to `pdp.audit_log`.

Replacement chain visible on the source move form via
`x_custom_coretax_replaced_by_id` (and inverse on the replacement).

## Pre-Export Validation

`tax.bulk.validation.wizard` runs sanity checks for a batch of moves
before opening the Coretax export wizard. Checks:

- Partner NPWP format (15 or 16 digits) + presence per move type.
- Individual partner NIK format.
- DPP > 0.
- Sertel attached to company's Coretax config + not expired.

Returns an HTML report listing each move and its issues so operators
can fix in bulk before submission.

## Partner Extensions

- `x_custom_npwp` — text, accepts dotted/dashed format, auto-validates
  to 15- or 16-digit normalised form.
- `x_custom_npwp_status` — computed: valid / invalid / none.
- `x_custom_nik` — 16-digit (orang pribadi).
- `x_custom_pkp` — Pengusaha Kena Pajak flag.
- `x_custom_foreign_counterparty` — computed from country comparison.

## Security Groups

- `custom_tax_id.group_tax_id_user` — read rules, see withholding lines
  on bills, run pre-export validation.
- `custom_tax_id.group_tax_id_admin` — configure rules + DPP methods,
  issue Faktur Pengganti. Inherits user.

## Audit

All withholding events + Faktur Pengganti relinks → `pdp.audit_log`:

- `pph_withholding_applied`
- `faktur_pengganti_issued`

## Dependencies

- `custom_core`, `custom_pdp_taxonomy`, `custom_pdp_audit`
- `custom_coretax` (for `custom.coretax.bukti.potong` + adapter)
- `custom_accounting_full` (for Indonesian COA reference)
- Odoo: `account`, `purchase`, `product`

## Install

```bash
make install MODULE=custom_tax_id DB=<tenant_db>
```

Post-install steps:

1. Open **Settings → Pajak Indonesia → Withholding Rules** and bind each
   seeded rule to your company's PPh hutang account, then activate.
2. For PPN DPP Nilai Lain, edit the relevant `account.tax` records
   (Accounting → Configuration → Taxes) and switch to Nilai Lain method.
3. Partners get NPWP fields auto-validated on save.

## Roadmap (not in P2B)

- PPh 21 (employee payroll withholding) — handled by
  `custom_hr_payroll_id` (P2C).
- Auto-creation of `account.move.line` for the hutang PPh side of the
  withholding journal (currently relies on accountant booking the
  contra-entry). Holding off pending review of standard Odoo accounting
  posting hooks.
- Per-product category withholding override resolver (currently uses
  partner + rule priority).
