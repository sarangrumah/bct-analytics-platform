# Custom PPOB Suite — Knowledge

Ported from the ERA PPOB R&D project (`E:\Projects\Odoo\rnd-ppob`, Odoo 18) into
the Odoo 19 platform as the `ppob` industry vertical. This file documents the
whole suite; each module's `__manifest__.py` description covers its own scope.

## Purpose

PPOB (Payment Point Online Bank) = bill-payment / top-up reseller. Mitra (B2B
resellers) hold prepaid **wallets**; we hold prepaid **deposit inventory** with
each **provider**. A **transaction** debits the mitra wallet and the provider
bucket atomically, then calls the provider adapter. Tax (PMK-63 margin VAT),
commissions (with PPh 23), a daily e-Faktur rollup, and bank virtual-account
top-ups sit on top.

## Modules (dependency order)

1. `custom_ppob_core` — product classes (vat_mode), products, price tiers,
   res.partner mitra/provider fields, sequences, security groups, **COA
   scaffolding** + role→account mapping.
2. `custom_ppob_wallet` — `custom.ppob.wallet` + atomic `_atomic_debit` /
   `_atomic_credit` / `_atomic_credit_with_tax` primitives (row-locked, paired
   GL + sub-ledger).
3. `custom_ppob_provider` — provider master, atomic bucket inventory, SKU map,
   adapter registry (`ppob_mock`, `ppob_http_json`), DP-100% topup wizard.
4. `custom_ppob_sale` — transaction state machine + dispatch + reaper; **merged
   tax** (vat_mode/dpp/ppn).
5. `custom_ppob_sla` — declarative throughput/latency targets (provider × class,
   wildcard fallback) + hourly throughput samples holding **both** the Oracle
   historical baseline and Odoo actuals.
6. `custom_ppob_commission` — commission rules/accruals + settlement (PPh 23 via
   `custom_pph_witholding`).
7. `custom_ppob_rollup` — daily aggregation → summary faktur for e-Faktur.
8. `custom_ppob_va` — mitra virtual accounts, H2H callbacks, `va_match` reconcile.
9. `custom_ppob_oracle_bridge` — legacy Oracle EVShop bridge (opt-in, not in the
   default pack).
10. `custom_ppob_biller_digiflazz` — first concrete biller adapter
    (`ppob_digiflazz`). Per-tenant opt-in, not in the pack: which biller a tenant
    sells through is a commercial decision. **Never run against a live server** —
    written to the published spec, tested against a mocked HTTP layer.

## Key design decisions (vs ERA source)

- **Account scaffolding (D5):** accounts are created idempotently in
  `custom_ppob_core` `post_init_hook` (search-by-code-then-create with
  `company_ids`), NOT XML data — avoids Odoo-19 `company_ids` M2M + non-idempotent
  `-u` hazards. Downstream code resolves accounts by role via
  `custom.ppob.account.mapping._get_account(role, company)`, never by xmlid.
- **Posting model (D7):** a sale's GL is posted by the wallet + bucket atomic
  helpers (each posts its own paired entry). There is **no** separate compound
  sale move — the ERA `_post_sale_move`/`_prepare_account_move_vals` were dead
  code and are gone. Per-transaction revenue is booked **gross**; PPN is
  recognised at the daily rollup faktur, not per transaction.
- **Adapters (D1):** the PPOB adapter registry is separate from
  `custom_adapter_framework` (provider-instantiated, domain verbs
  inquiry/pay/status/topup). `ppob_http_json` does a single signed POST per call
  (no retry loop) so a non-idempotent `pay()` cannot double-sell; it reuses
  `custom.adapter.config` for per-tenant base_url/credential and writes
  `custom.adapter.call.log`.
- **`AdapterResult.ok` is TRI-STATE.** `True` = settle, `False` = confirmed
  failure (refund), **`None` = provider still processing (leave alone)**. Both
  `_dispatch_one` and the reaper check `is None` FIRST, because `if result.ok:`
  and `not result.ok` cannot tell None from False — and getting that wrong
  refunds the mitra on a sale the provider then fulfils, i.e. we pay twice.
  Any adapter for an async provider must return `ok=None` for pending rather
  than guessing.
- **DP topup (D2):** the wizard is self-contained (DP + Pelunasan `in_invoice`
  pair + `account.move._post` hook advancing the bucket). No third-party
  advance-payment module.
- **Rollup × reports (D6):** the summary faktur posts to a journal flagged
  `account.journal.x_custom_report_excluded` (a generic flag added to
  `custom_accounting_reports`), so its GL is omitted from TB/P&L/BS — revenue is
  already booked per transaction.
- **VA replay (D3):** the H2H controller reuses `custom_core`'s Redis-backed
  `_NonceStore` + IP allowlist. The **hard** idempotency guarantee is the DB
  `UNIQUE(bank_ref)` on `custom.ppob.va.topup`; the nonce is a throttle only.
- **SLA targets are configuration, not constants (project D4):** throughput and
  latency targets live in `custom.ppob.sla.target`, scoped provider × class with
  wildcard fallback (most specific wins). The seeded baseline is **derived, not
  measured** — `active_hours` and `peak_factor` are visible editable fields, not
  hidden constants, and `calibration_source` keeps a guess labelled a guess until
  a human promotes it. The targets are **declarative only**: nothing in dispatch
  reads them to throttle or reject. `timeout_s_target` and `max_in_flight` in
  particular record what SHOULD hold — the effective timeout is still
  `custom.adapter.config.timeout_s`, and no concurrency cap exists anywhere.

## Gotchas

- Raw-SQL balance updates in wallet/bucket reference physical table names
  (`custom_ppob_wallet`, `custom_ppob_provider_bucket`) — keep in sync with model
  renames. The bucket's partial unique indexes are created in `init()`.
- Wallet/bucket sub-ledgers guard optional back-ref columns
  (`ppob_transaction_id`, `va_topup_id`) against `_fields` so they install before
  the modules that add those columns.
- Financial reports now exclude any journal with `x_custom_report_excluded=True`
  (backward-compatible; defaults False).
- `custom_ppob_oracle_bridge` runs two 1-minute crons — keep it out of tenants
  that don't use the legacy EVShop pipeline (it is excluded from the seed pack).
- `custom.adapter.config.retry_count` / `circuit_breaker_threshold` /
  `circuit_breaker_cooldown_s` are **visible in the UI but inert for PPOB** — the
  suite deliberately skips the framework's retry loop (D1) and reads only
  `base_url` / `credential_ref` / `timeout_s`. Setting them does nothing.
- `custom.ppob.transaction.provider_latency_ms` is adapter RTT, measured around
  the `pay()`/`inquiry()` call only. Do NOT use `completed_at - dispatched_at` as
  latency: on cron-polled paths (oracle_bridge, pos ingest) that delta is
  dominated by cron lag, not the provider.
- `custom.ppob.throughput.sample` reads transactions via raw SQL and therefore
  calls `env.flush_all()` first — same rule as the raw-SQL financial reports.
  Removing that flush silently drops transactions written earlier in the cursor.
- **This module's groups sit on its own `custom_ppob_core.res_groups_privilege_ppob` privilege** ("PPOB") — Odoo 19 renders every group sharing one `res.groups.privilege` as a single pick-one dropdown on the user form, so a privilege shared across modules makes saving a user silently drop the other modules' groups. The privilege carries `custom_core.module_category_custom_platform`, so the selector still appears alongside the other custom modules. Do not point new groups at `custom_core.res_groups_privilege_custom_platform`.

## Verification

Dev stack ports 18069/18072. From Git Bash:
`MSYS_NO_PATHCONV=1 ... odoo --test-tags /custom_ppob_provider,/custom_ppob_sale,/custom_ppob_va,/custom_ppob_oracle_bridge -d <db> --stop-after-init`.
Restart the Odoo container after Python changes. See the ERA source at
`E:\Projects\Odoo\rnd-ppob\addons\era_ppob_*` for the pre-port reference.
