# the tenant Opening Balances (31 May 2026)

Loads the beginning balances for the two the tenant companies as **posted opening
journal entries**, one per company, dated **2026-05-31**, ref **"Saldo Awal 31 Mei
2026"**.

| Company | Odoo | Lines | Total (Rp) |
|---|---|--:|--:|
| PT Aero Inovasi Media (the tenant) | company by name | 27 | 43,264,095,722 |
| PT Aero Reksa Kreasi Angkasa (the tenant) | company by name | 12 | 5,054,276,231 |

Both trial balances balance exactly (Debit = Credit). Source: Google Drive
`Beg Balance the tenant.xlsx` (sheets `TB the tenant` / `TB the tenant`).

## What it does (`post_init_hook`)

1. **Creates 5 missing bank/deposit accounts** (all `asset_cash`) if absent:
   - the tenant: `1103019270`, `1103019280`
   - the tenant: `1103019290`, `1103019300`, `1105020007`
2. **Posts one opening journal entry per company** into the general (Miscellaneous)
   journal, resolving accounts by **(company, code)** at runtime.

## Design notes

- Companies are resolved **by name**, not by hardcoded id.
- Accounts are resolved by **code within the company's `company_ids`** — the two
  companies use different account namespaces (the tenant `tenant.coa_*`, the tenant
  `account.2_id_coa_10d_*`), so fixed external ids are not portable. Resolving by code
  works on the clone, UAT and prod.
- **Idempotent**: skips a company whose opening move already exists, and only creates
  accounts that don't exist yet. Safe to re-run on upgrade.
- **Mid-year cutover**: the TB includes YTD P&L accounts (5xxx/7xxx), so the 2026
  Balance Sheet & P&L are complete from January. `3006100001 Retained earnings -
  beginning` is a separate line (prior-year accumulation) — do not net YTD P&L into it.

## Data

- `data/opening_balance_aim.csv`  — `code,name,debit,credit` (27 rows)
- `data/opening_balance_tenant.csv` — `code,name,debit,credit` (12 rows)
- `data/missing_accounts.csv`     — `company_name,code,name,account_type,reconcile`

## Rollout

1. Test DB `trn_tenant_begbal` (clone of prod) — verified.
2. Prod `prd_tenant`: currently has 0 journal entries and lacks the same 5 accounts.
   Install this module to post the opening balances. Verify the Trial Balance totals
   match the table above and the intercompany related-party balance (6,337,500) ties
   out between the tenant (`1110000001`) and the tenant (`2103400001`).
