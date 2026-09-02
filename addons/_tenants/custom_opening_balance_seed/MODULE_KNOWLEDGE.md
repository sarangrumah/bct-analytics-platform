---
status: draft
generated_at: 2026-07-02T08:46:25Z
generator: bootstrap-v1
module: custom_opening_balance_seed
manifest_version: 19.0.1.0.0
---

# the tenant Opening Balances (31 May 2026)

## Purpose
This module loads the beginning balances for PT Aero Inovasi Media (the tenant) and
PT Aero Reksa Kreasi Angkasa (the tenant) as of 31 May 2026. It creates a handful of
missing bank/deposit accounts, then posts one balanced opening journal entry per
company. It defines no models, wizards, views, or controllers — all behavior runs
at install time via a `post_init_hook`.

## Business Flow
Everything below executes inside `post_init_hook` (hooks.py:127) when the module
is installed/upgraded:
1. `_ensure_missing_accounts` reads `data/missing_accounts.csv` and creates any of
   the 5 `asset_cash` accounts that do not yet exist for their company
   (the tenant: 1103019270, 1103019280; the tenant: 1103019290, 1103019300, 1105020007).
   Existing accounts are skipped (hooks.py:58).
2. For each company (resolved by name), `_post_company_opening` reads the
   per-company opening CSV, resolves each row's account by (company, code), builds
   the journal lines, and creates + posts one `account.move`.
3. The move is posted (`action_post`) on 31 May 2026 with ref
   "Saldo Awal 31 Mei 2026", on the company's `general` (Miscellaneous) journal.

Note: the hook does NOT verify that debits equal credits. After posting it only
sums the debit side to emit a log line (hooks.py:120-124).

## Key Models
- **None** — this module defines no classes and no models. It only *creates*
  records of existing models (`account.account`, `account.move`) via `.create`.

## Important Fields
- None. No fields are defined or extended.

## Public Methods
- **`post_init_hook(env)`** (hooks.py:127, registered in `__manifest__.py:35`) —
  the module's single entry point and core mechanism. It is invoked automatically
  by Odoo at install/upgrade; there is no user-facing API.

## Integration Points
- **Depends on:** `account` (only) — `__manifest__.py:31-33`.
- **Inherits from:** None. No model inheritance.
- **Extended by:** None.
- **External calls:** None. CSVs are read from the module's own `data/` dir via
  `file_open`.

## Gotchas
- **Idempotent.** A company whose opening move (ref "Saldo Awal 31 Mei 2026")
  already exists is skipped (hooks.py:75-83); already-present accounts are skipped
  (hooks.py:58). Safe to re-run on upgrade.
- **Hard-fails on missing general journal.** Raises `UserError` if a target company
  has no `general` (Miscellaneous) journal (hooks.py:90-93).
- **Hard-fails on unresolved codes.** Raises `UserError` if any CSV account code
  cannot be resolved for the company (hooks.py:107-110).
- **CSVs read directly (not ORM/XML) by design.** Accounts are resolved by
  (company, code) at runtime rather than by fixed external ids, because the two
  companies use different account namespaces (the tenant: `tenant.coa_*`,
  the tenant: `account.2_id_coa_10d_*`). This keeps the module portable across clone/UAT/prod
  databases (hooks.py:1-9).
- Companies are resolved by name; a missing company logs a warning and is skipped
  rather than failing.

## Data Summary
- **the tenant:** 27 lines, Rp 43,264,095,722 (balanced).
- **the tenant:** 12 lines, Rp 5,054,276,231 (balanced).

## Out of Scope
- This module does not cover any additional business logic or data management
  beyond creating the 5 missing accounts and posting the two opening moves. New
  financial requirements would call for a separate module.
