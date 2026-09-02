---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_bank_import
manifest_version: 19.0.0.3.0
---

# custom_bank_import

## Purpose
Two complementary statement-ingestion pipelines for Indonesian banks (BCA, Mandiri, BNI, BRI, CIMB, Permata, Danamon + generic): (1) CSV/XLSX template-based wizard import where per-bank parsing rules are declared on `custom.bank.import.template`, and (2) host-to-host (H2H) API sync where each bank ships an adapter (`bank_bca_h2h`, `bank_mandiri_h2h`, etc.) built on `custom_adapter_framework` (HMAC, retry, circuit breaker), polled by a configurable interval cron.

Both pipelines write to `account.bank.statement` / `account.bank.statement.line` and create a `custom.bank.import.log` row with SHA256 file-hash deduplication. Pairs naturally with `custom_accounting_full.custom.reconcile.rule` for downstream auto-matching.

## Business Flow
- **CSV pipeline**: Operator configures a `custom.bank.import.template` (`code`, `encoding`, `delimiter`, `date_format`, 1-based column indexes `date_column_index`/`ref_column_index`/`partner_column_index`/`amount_credit_column_index`/`amount_debit_column_index`/`balance_column_index`/`signed_amount_column_index`, decimal/thousand separators). Then opens `custom.bank.import.csv.wizard` and uploads file + selects journal + template.
- `action_import()`: computes `file_hash = sha256(bytes)`; if a prior log with same hash and state ∈ (imported, partial) exists → `UserError`. Calls `template.parse_csv(b64)` which returns `{lines, errors, total_rows}`. Each line is `{date, ref, partner_hint, amount (Decimal), balance}`; amount = `signed_amount` if `signed_amount_column_index>0`, else `credit - debit`. Zero-amount lines are skipped. Creates `account.bank.statement` + bulk `account.bank.statement.line` records; writes a `custom.bank.import.log` with state imported/partial/failed.
- **H2H pipeline**: Operator creates `custom.bank.h2h.connection` (`bank_code`, `adapter_config_id` referring to `custom.adapter.config`, `account_number`, `journal_id`, `sync_interval_minutes`). `action_sync_now()` or cron `_cron_sync_due()` calls `_do_sync()` which fetches adapter via `adapter_config_id.get_adapter()`, calls `adapter.inquiry_statement(account_number, date_from=last_sync_at, date_to=now)`. Lines from `AdapterResponse.data['lines']` are persisted via `_persist_lines` → one `account.bank.statement` + many `account.bank.statement.line` + a log row referencing a per-bank pseudo-template (`h2h_<bank_lower>` auto-created via `_h2h_pseudo_template`).
- Adapter implementations: `BcaH2HAdapter` knows BCA's `/banking/v3/corporates/accounts/{acct}/statements` path and normalises `Data: [{TransactionDate, Amount, TransactionType: D|C, ...}]` → internal `{date, description, ref, amount}` (sign-flipped on D). `GenericBankH2HAdapter` reads paths from `ir.config_parameter` keys `custom_bank_import.<adapter>.path_{balance,statement}`. Mandiri/BNI/BRI/CIMB/Permata/Danamon currently alias the generic adapter (placeholder until per-bank canonical signing is wired).
- Errors / circuit-breaker state are recorded on `custom.bank.h2h.connection.status` ∈ active/paused/error + `last_error`.

## Key Models
- `custom.bank.import.template` — Declarative parser config; one per (bank, layout, company).
- `custom.bank.import.log` — Audit row per import attempt. `state` ∈ imported/failed/partial. Tracks `file_hash` for dedup. Inherits `mail.thread`.
- `custom.bank.h2h.connection` — Per-account H2H credentials + journal + sync schedule. Inherits `mail.thread`.
- `BcaH2HAdapter` / `GenericBankH2HAdapter` (+ Mandiri/BNI/BRI/CIMB/Permata/Danamon aliases) — Python adapter classes (NOT Odoo models); registered via `@register_adapter("bank_<code>_h2h")` from `custom_adapter_framework`.
- `custom.bank.import.csv.wizard` (TransientModel) — Upload wizard.

## Important Fields
- `custom.bank.import.template.code` (Char, indexed, unique-per-company) — stable parser identifier (e.g. `bca_csv`).
- `custom.bank.import.template.encoding` (Selection utf-8/latin-1) / `delimiter` (Char size=1) / `has_header` (Boolean).
- `custom.bank.import.template.date_format` (Char, Python `strptime` format) — e.g. `%d/%m/%Y` (BCA), `%d-%m-%Y` (Mandiri).
- `custom.bank.import.template.*_column_index` (Integer, 1-based; `-1` = unused).
- `custom.bank.import.template.signed_amount_column_index` (Integer) — when > 0 overrides credit/debit split.
- `custom.bank.import.template.decimal_separator` / `thousand_separator` (Char, size=1).
- `custom.bank.import.log.file_hash` (Char, indexed) — SHA256 of raw bytes; dedup key.
- `custom.bank.import.log.state` (Selection imported/failed/partial, required, indexed).
- `custom.bank.import.log.line_count` / `error_count` (Integer).
- `custom.bank.import.log.raw_payload` (Text) — H2H raw response or CSV row-error summary (capped 8000 chars).
- `custom.bank.h2h.connection.bank_code` (Selection BCA/Mandiri/BNI/BRI/CIMB/Permata/Danamon/Other).
- `custom.bank.h2h.connection.adapter_config_id` (M2o `custom.adapter.config`, required) — provides base_url, auth, secret, breaker config.
- `custom.bank.h2h.connection.sync_interval_minutes` (Integer, default 60) — cron throttle.
- `custom.bank.h2h.connection.last_sync_at` (Datetime, readonly) — used as `date_from` for next call.
- `custom.bank.h2h.connection.status` (Selection active/paused/error, tracking).
- Unique constraint: `unique(bank_code, account_number, company_id)` on H2H connections.

## Public Methods
- `custom.bank.import.template.parse_csv(file_b64)` — Returns `{lines, errors, total_rows}`.
- `custom.bank.import.template._parse_amount(raw)` / `_parse_date(raw)` / `_read_csv(file_bytes)` — Parser internals.
- `custom.bank.import.csv.wizard.action_import()` — Wizard entry; raises UserError on dedup hit.
- `custom.bank.h2h.connection.action_sync_now()` / `_do_sync()` / `_persist_lines(lines, raw_payload)`.
- `custom.bank.h2h.connection._cron_sync_due()` (`@api.model`) — Cron entry; honours per-connection `sync_interval_minutes`.
- Adapter classes: `inquiry_balance(account_number)`, `inquiry_statement(account_number, date_from, date_to)` returning `AdapterResponse(ok, data={...lines...}, error)`.
- `BcaH2HAdapter._normalize_lines(payload)` — Maps BCA's `TransactionDate`/`Amount`/`TransactionType` to internal shape.
- `GenericBankH2HAdapter._path(suffix, default)` — Reads `ir.config_parameter` per-adapter.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_adapter_framework`, `account`.
- **Inherits from:** `mail.thread` on log + H2H connection.
- **Extended by:** Vertical modules can `@register_adapter(...)` a new bank adapter without changing this module.
- **External calls:** HTTP to each bank's H2H endpoint via `custom_adapter_framework.BaseAdapter.call()` (HMAC signed, retry, circuit breaker).
- **Cross-vertical:** generic — every Indonesian tenant needs bank import.
- **Downstream:** `custom_accounting_full.custom.reconcile.rule._cron_auto_reconcile` consumes the produced `account.bank.statement.line` rows.

## Gotchas
- **Mandiri/BNI/BRI/CIMB/Permata/Danamon adapters are placeholders** — they're aliases of `GenericBankH2HAdapter` with distinct `adapter_type` names (so breakers track separately) but use the same canonical-form signing path. Per-bank production wiring is deferred.
- **BCA adapter notes `_sign_request` is an inherit point** — production code must override for the strict `METHOD:Path:AccessToken:LowerCase(SHA256(Body)):Timestamp` canonical form; the framework's HMAC signer covers only the simpler `ts || body` shape.
- **File-hash dedup blocks re-import even for legitimately edited files** — the only way to re-import is to archive the prior log first.
- **Zero-amount lines silently dropped** — `if amount == Decimal("0"): continue` — fee-only or memo entries are lost.
- **CSV parser does NOT support XLSX despite manifest claim** — only `csv.reader`. XLSX paths would need `openpyxl` integration.
- **H2H `_persist_lines` creates a NEW `account.bank.statement` PER SYNC** (`name = "H2H <bank> <date>"`) — multiple syncs same day produce multiple statements; consolidation is the operator's problem.
- **`_h2h_pseudo_template` creates a per-bank shadow template** to satisfy the log's required `template_id` — these show up in the template list as "H2H Pseudo — BCA" etc.
- **`status='error'` is sticky** — `_do_sync` only flips back to `active` on successful sync; a paused/error connection requires manual `action_sync_now` or UI reset to retry (the cron skips non-active connections via `[("status", "=", "active")]`).
- **`adapter.inquiry_statement` date range** uses `last_sync_at.date()` or last 24h — if cron stops for a week, only the last 24h are pulled on resume; older days are lost unless the operator manually back-fills.
- **`_parse_date` returns False on parse failure**; the row is then added to `errors` and skipped — no partial parsing of malformed dates.
- **File container is sniffed by magic bytes, not extension** — `_read_rows` routes OLE2 (`D0 CF 11 E0…`) to xlrd, zip (`PK`) to openpyxl (first sheet, `data_only`), anything else to the CSV reader. `.xls`/`.xlsx` uploads therefore work through the same wizard; `_parse_date` accepts native `datetime`/`date` cells.
- **Three more bank layouts are auto-detected** (like BCA-corp, independent of template config): BRI "BRISIM" XLSX (`Tanggal|Uraian|Teller|Debet|Kredit|Saldo` header — the export is column-shifted: only Tanggal+Uraian are trustworthy, the debet/kredit/saldo triple is regex-extracted from inside the Uraian text, and a single Uraian can embed a second timestamped transaction e.g. "DEBET BY CEK"; NB the export itself omits rows — its running-saldo chain has gaps — so totals may not tie to the real mutasi), BNI "TRANSACTION INQUIRY" XLS (header row located by labels `Post Date`/`Db/Cr`/`Amount`; native datetime cells; sign from Db/Cr C/D), and Mandiri "Acc_Statement" XLS (labels `Posting Date`/`Remark`/`Debit`/`Credit`; all string cells, `dd/mm/YYYY HH:MM:SS` dates, US-separator amounts; `No of Debit` summary block terminates parsing).
- **Accounting date = bank transaction date, even in soft-locked periods** — core `_post()` silently shifts a statement-line move dated on/before `fiscalyear_lock_date` to today; the wizard writes the parsed transaction date back afterwards with `bypass_lock_check=BYPASS_LOCK_CHECK` (grouped per date). Dates on/before `hard_lock_date` refuse the whole import up front with a UserError.
- **BCA corporate ("KlikBCA Bisnis" / CorpAcctTrxn) CSV is auto-detected** — `parse_csv` sniffs the signature (`Informasi Rekening` preamble or the `Tanggal Transaksi,Keterangan,Cabang,Jumlah,Saldo` header) and routes to `_parse_bca_corp`, ignoring the template's column-index/date-format config. That layout can't use the generic model: dates are `DD/MM` with the year only in the `Periode :` preamble, and amount+sign are fused in one `Jumlah` cell (`30,000.00 DB` / `10,930,941.82 CR`; CR→+ inflow, DB→− outflow). `Saldo Awal/Mutasi/Saldo Akhir` footer rows terminate parsing. So a mis-mapped "BCA CSV" template (e.g. `%d/%m/%Y` on yearless dates, no amount column) still parses correctly.
- **`(amount)` parentheses-style negatives** are recognised; trailing `-` (e.g. `1234.56-`) is NOT.
- **Encoding fallback is `errors="replace"`** — non-UTF-8 bytes silently become `�`, corrupting refs.

## Out of Scope
- **OFX / MT940 / CAMT.053 parsers** — only CSV.
- **Multi-currency statement parsing** — `currency_id` of the statement line follows the journal.
- **Automatic reconciliation** — see `custom_accounting_full.custom.reconcile.rule`.
- **Bank fee splitting / standing-order detection** — out of scope.
- **OAuth2 / OpenID flows** — adapter framework handles HMAC; OAuth-based banks (rare in ID corporate H2H) need custom adapter.
- **Statement attachment storage** — file is read and discarded; raw file is not persisted on the log row.
