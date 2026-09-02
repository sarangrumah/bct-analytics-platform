{
    "name": "the tenant Opening Balances (31 May 2026)",
    "version": "19.0.2.0.0",
    "summary": "Beginning balances for the tenant & the tenant companies as of 31 May 2026.",
    "description": """
the tenant Opening Balances
=========================

Loads the beginning balances (per 31 May 2026) for the two the tenant companies as
posted opening journal entries, one per company:

- PT Aero Inovasi Media (the tenant)        -> 27 lines, Rp 43,264,095,722
- PT Aero Reksa Kreasi Angkasa (the tenant) -> 12 lines, Rp 5,054,276,231

Both trial balances balance exactly (Debit = Credit). The mid-year cutover keeps the
YTD P&L accounts (5xxx/7xxx) so the 2026 Balance Sheet & P&L are complete from January.

The post-init hook also creates 5 bank/deposit accounts that are missing from the
target chart (all asset_cash):
- the tenant:  1103019270, 1103019280
- the tenant: 1103019290, 1103019300, 1105020007

Companies are resolved by NAME (not hardcoded id). The hook is idempotent: it skips a
company whose opening move (ref "Saldo Awal 31 Mei 2026") already exists, and only
creates accounts that are not already present. Safe to re-run on module upgrade.

Source: Google Drive "Beg Balance the tenant.xlsx" (TB the tenant / TB the tenant sheets).
""",
    "author": "Platform",
    "category": "Tenants/the tenant",
    "depends": [
        "account",
    ],
    "data": [],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "auto_install": False,
    "license": "LGPL-3",
}
