# Custom HR Payroll (Indonesia)

Self-contained Indonesian payroll engine — does **not** require Odoo EE
`hr_payroll`. Implements current statutory regime: PP 58/2023 (TER) for
monthly withholding, UU HPP 2021 progressive brackets for annual
reconciliation, BPJS Kes/TK per current PMK/Perpres.

## Calculation Methods

### TER (default, since Jan 2024)

Per PP 58/2023, monthly PPh 21 for Pegawai Tetap uses a flat rate from
a table indexed by Kategori (A/B/C) and monthly gross income. The
category derives from PTKP status:

| Kategori | PTKP Statuses |
|----------|---------------|
| A | TK/0, TK/1, K/0 |
| B | TK/2, TK/3, K/1, K/2 |
| C | K/3, K/I/0..3 |

The TER table is stored in `hr.payroll.ter.bracket`. Seeded data covers
all three categories per PP 58/2023 Lampiran. Operators may edit when
DJP updates the table.

### Annual Reconciliation (December)

In December and for any non-Pegawai Tetap employment type, the engine
falls through to **annualised UU HPP progressive**:

| Bracket | Rate |
|---------|------|
| ≤ 60 jt | 5% |
| 60-250 jt | 15% |
| 250-500 jt | 25% |
| 500 jt - 5 M | 30% |
| > 5 M | 35% |

Year-end deducts biaya jabatan (5%, max Rp 6 jt/year), JHT employee,
JP employee, PTKP. The delta vs sum of monthly TER deductions appears
on the SPT 1721 A1 as kurang/lebih bayar.

## BPJS

| Program | Employee | Company | Cap |
|---------|----------|---------|------|
| Kesehatan | 1% | 4% | Rp 12 jt |
| JHT | 2% | 3.7% | — |
| JP | 1% | 2% | Rp 10,042,300 (2025) |
| JKK | — | 0.24%–1.74% (industry) | — |
| JKM | — | 0.3% | — |

## Models

- `hr.payroll.config` — singleton-per-company config with calc_method
  selector, PTKP table, biaya jabatan, BPJS rates + ceilings.
- `hr.payroll.ter.bracket` — TER table rows (category × lower/upper × rate).
- `hr.employee` extension — NIK, NPWP, KK, PTKP status, **TER category
  (computed)**, employment type, BPJS numbers, bank.
- `hr.payslip` — main payslip with TER + annual fallback compute engine,
  Bupot PPh 21 materialisation on approve.
- `hr.payslip.line` — line-by-line breakdown.

## Wizards

- **Run Batch Payroll** (`hr.payslip.batch.wizard`) — pick period +
  employees (empty = all), creates + computes + optionally auto-approves.
- **Generate SPT 1721 A1** (`hr.payroll.spt.a1.wizard`) — annual report
  per employee with kurang/lebih bayar, PDF (PT.A1 form layout) + XML
  batch for Coretax upload.

## Coretax Integration

On payslip `approve`, a draft `custom.coretax.bukti.potong`
(`jenis_pph = pph_21`) is created with the period, partner, DPP (gross),
and PPh terpotong. Coretax export wizards (in `custom_coretax`) then
bundle these into the monthly XML submission per PER-04/PJ/2023.

## Security Groups

- `custom_hr_payroll_id.group_user` — read payslips, config, TER.
- `custom_hr_payroll_id.group_manager` — manage payslips, run batch,
  generate SPT 1721 A1, edit config + TER.

## Audit

Every state change (compute / approve / pay) writes to `pdp.audit_log`
via the chained hash mechanism.

## Dependencies

- `custom_core`, `custom_pdp_taxonomy`, `custom_pdp_audit`
- `custom_coretax` (for Bupot PPh 21 model)
- Odoo: `hr`, `mail`

## Install

```bash
make install MODULE=custom_hr_payroll_id DB=<tenant_db>
```

Post-install: open **Settings → Payroll ID → Payroll Config**, verify
PTKP / BPJS values match current regulation. Edit TER bracket entries
under **Configuration → TER Brackets** when DJP issues an update.

## Roadmap

- Contract-based salary (currently sourced from employee record).
- Variable allowances + bonuses with separate tax treatment.
- Stock option taxation (PP 76/2023).
- Employee self-service portal for slip download.
- Integration with `custom_accounting_full` to auto-post payroll
  journal entries (currently manual).
