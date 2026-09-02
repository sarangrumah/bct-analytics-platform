# custom_tenant_coa_seed

Tenant-specific seed for **`erp_dev_tenant`**. Loads the tenant's Chart of
Accounts (548 accounts, 10-digit codes), VAT (PPN) and withholding (PPh) taxes,
and Indonesian fiscal positions, then wires them as defaults across the
relevant Odoo modules.

> ⚠️ Do **not** install this on any other tenant DB or on the generic platform.
> The CoA is specific to the tenant and will conflict with `custom_accounting_full`'s
> generic PSAK template.

## Data source

Extracted from `docs/the tenant-Master-Data-Template.xlsx`, sheet `06_COA`,
rows 37 onwards.

## External-ID convention

`account_tenant_<10-digit-code>`  e.g. `account_tenant_1102000001` for
`Cash on hand - IDR`.

## Account-type mapping

The source sheet mixes Odoo enum keys with human-readable labels. Mapping
applied during generation:

| Source label              | Odoo `account_type`       |
|---------------------------|---------------------------|
| `Cost of Revenue`         | `expense_direct_cost`     |
| `Current Assets`          | `asset_current`           |
| `Current Liabilities`     | `liability_current`       |
| `Current Year Earnings`   | `equity_unaffected`       |
| `Depreciation`            | `expense_depreciation`    |
| `Equity`                  | `equity`                  |
| `Expenses`                | `expense`                 |
| `Fixed Assets`            | `asset_fixed`             |
| `Income`                  | `income`                  |
| `Non-current Assets`      | `asset_non_current`       |
| `Non-current Liabilities` | `liability_non_current`   |
| `Other Income`            | `income_other`            |

Six source rows with empty `account_type` are fallback-mapped by code prefix
(`75xx`, `83xx` → `income_other`; `88xx`, `89xx` → `equity`).

## Overrides

`2103100001 Trade Payables - Third parties` is force-set to `liability_payable`
(reconcile=True) because Odoo requires this type for the partner default-payable
property.

`asset_receivable` and `liability_payable` accounts have `reconcile=True`
enforced (Odoo constraint).

## Default-account wiring (post-install hook)

| Role                                       | Account                                                    |
|--------------------------------------------|------------------------------------------------------------|
| Company currency                           | IDR                                                        |
| Company fiscal country                     | Indonesia                                                  |
| Income currency exchange                   | `7607000000` Difference of Foreign Exchange income         |
| Expense currency exchange                  | `7704000000` Difference of Foreign Exchange expense        |
| Default customer receivable (res.partner)  | `1106000001` Trade Receivables - Third Parties             |
| Default vendor payable (res.partner)       | `2103100001` Trade Payables - Third parties                |
| Product category income                    | `5199000000` Gross Sales-Others                            |
| Product category expense / COGS            | `6199000000` COGS-Others                                   |
| Stock input (if stock installed)           | `2103109199` GR/IR clearing-Tr Pay-Third P-Others          |
| Stock output (if stock installed)          | `2103109199` GR/IR clearing-Tr Pay-Third P-Others          |
| Stock valuation (if stock installed)       | `1113100099` Inventory-Others                              |
| Default cash journal account               | `1102000001` Cash on hand - IDR                            |
| Default bank journal account               | `1103019300` BCA Main Bank                                 |

## Taxes (`account.tax.csv`)

| External ID                         | Name                            |
|-------------------------------------|---------------------------------|
| `tenant_tax_ppn_keluaran_11`          | PPN Keluaran 11% → `2104300001` |
| `tenant_tax_ppn_masukan_11`           | PPN Masukan 11% → `1117200001`  |
| `tenant_tax_ppn_keluaran_12`          | PPN Keluaran 12% (inactive)     |
| `tenant_tax_ppn_masukan_12`           | PPN Masukan 12% (inactive)      |
| `tenant_tax_pph_21`                   | PPh 21 → `2104100003`           |
| `tenant_tax_pph_22`                   | PPh 22 → `2104100004`           |
| `tenant_tax_pph_23_2pct`              | PPh 23 2% → `2104100005`        |
| `tenant_tax_pph_23_4pct`              | PPh 23 4% → `2104100005`        |
| `tenant_tax_pph_4_2_10pct`            | PPh Final 4(2) 10% → `2104100001`|
| `tenant_tax_pph_26_20pct`             | PPh 26 20% → `2104100008`       |

## Fiscal positions

| External ID                  | Auto-apply | Substitution                                |
|------------------------------|------------|---------------------------------------------|
| `tenant_fp_domestic`           | yes (ID)   | (none)                                      |
| `tenant_fp_vendor_non_pkp`     | no         | PPN Masukan 11% removed                     |
| `tenant_fp_vendor_no_npwp`     | no         | PPh 23 2% → PPh 23 4%                       |
| `tenant_fp_foreign_vendor`     | no         | PPh 23 2% → PPh 26 20%; PPN Masukan removed |

## Install

```bash
# Inside the erp_dev_tenant DB only:
make install MODULE=custom_tenant_coa_seed DB=erp_dev_tenant
```

After install, verify:
- Accounting → Configuration → Chart of Accounts: 548 entries with 10-digit codes.
- Settings → Companies → the tenant company: currency = IDR, fiscal country = Indonesia.
- A new partner has receivable `1106000001` and payable `2103100001` by default.
- A new product category inherits income `5199000000` and expense `6199000000`.
