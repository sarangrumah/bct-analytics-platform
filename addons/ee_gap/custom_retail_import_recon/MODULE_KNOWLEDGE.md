---
status: draft
generated_at: 2026-08-03T00:00:00Z
generator: claude-code-hand-authored-v1
module: custom_retail_import_recon
manifest_version: 19.0.1.0.0
---

# custom_retail_import_recon

## Purpose
Answers the question finance asks after every nightly retail import: *did everything the stores rang up land in Odoo, and if not, why not?* One read-only row per source transaction from the X24DN sales file, showing the transaction header, what the file said, what Odoo booked, the difference, and — for anything rejected — the importer's own reason.

Replaces the manual routine of exporting the POS list, exporting the X-Store file, and diffing them in a spreadsheet.

## Business Flow
- The nightly orchestrator imports X24DN and stages every row on `retail.import.line`.
- The accountant (or the operator who just ran an import) opens **Retail Import → X-Store Reconciliation**, or **Invoicing → Reporting → Reports → X-Store vs Odoo Reconciliation**.
- Default filters answer the two questions actually asked: *Not in Odoo* (`status in parked, missing`) and *Has a Difference* (`difference != 0`).
- A parked row carries the message the importer wrote, e.g. `produk belum teregister di master X101 (…)` or `no X70D tender (sync X70D first)`, so the fix is obvious without reading logs.

## Key Models
- `retail.import.recon` — **`_auto = False` SQL view.** No stored data of its own; every column is derived from `retail_import_line` + `retail_import_log` + `pos_order` at query time.

## Important Fields
- Header: `txn_ref` (`store-register-transaction`, the same reference `pos_order.pos_reference` carries), `store_code`, `store_name`, `trans_date`, `register`, `transnum`, `staff_name`, `member_id`, `transaction_note`.
- Source side: `line_count`, `source_qty`, `source_amount`, `source_tax`.
- Odoo side: `pos_order_id`, `odoo_amount`.
- Outcome: `difference` (source less Odoo), `status`, `reason`, `log_id`.

`status` answers *is this money in Odoo*, not *did the last run complain*: a transaction rejected by the latest import but present in Odoo anyway (posted by an earlier run, then re-imported into a failure) reads **posted**, with its `reason` still visible beside it.

## Integration Points
- **Depends on:** `custom_core`, `custom_retail_import` (the staged rows), `custom_accounting_reports` (parent menu for the accounting entry), `point_of_sale` (`pos_order`), `account`.
- **Reads:** `retail_import_line.raw_data_json`, `retail_import_log.file_type`, `pos_order`.
- **Writes:** nothing. Read-only ACL for all three groups.
- **Access:** `account.group_account_readonly` — which `group_account_user` implies and `group_account_manager` implies transitively, so one ACL row covers every accounting user — plus `custom_retail_import.group_retail_import_user` and this module's own `group_retail_recon_user`.

## Gotchas
- **Separate module on purpose.** It joins `pos_order`, so it needs `point_of_sale` — and `trn_tenant` runs `custom_retail_import` *without* POS. Folding this into the shared addon would force a POS install on that tenant. Keep it separate.
- **`json_to_record`, never repeated `->>`.** `raw_data_json` is `text`, so each `(raw_data_json::json)->>'k'` re-parses the whole document. Nine fields that way cost **8.8 s** over 48k rows; `json_to_record` parses once for **1.5 s**. Wrapping the cast in a LATERAL does **not** help — PostgreSQL inlines it and re-evaluates per reference. Casting to `jsonb` is worse again (**13 s**): the binary conversion costs more than the parse it saves.
- **A re-imported day appears under more than one log.** Summing across them double-counts every transaction of that day. `dense_rank() … ORDER BY log_id DESC` keeps only the newest log per transaction, which also gives the right status for a transaction parked on the first run and posted on the second.
- **`aggregate_key` is empty on rejected rows.** It looks like the natural key and is even indexed, but the importer only fills it once a row survives to posting — so precisely the rows this report exists for would be invisible. The key is rebuilt from the payload instead.
- **`aggregator=` is not a view attribute.** On a `<pivot>`/`<graph>` `<field>` the RNG rejects it (`Invalid attribute aggregator for element field`); it belongs on the model field definition. Float measures sum by default.
- **`res.users.groups_id` is `group_ids` in Odoo 19** — relevant when writing access checks against this model.
- The view covers `file_type = 'x24'` only. X48 returns are a separate flow and are not folded in here.

## Out of Scope
- Any writing, reprocessing or re-import action — this is a read-only lens. Re-importing stays with `custom_retail_import`.
- Tender (X70D) and discount (X31) reconciliation; only the sales file is compared.
- The POS Suspense Clearing residual, which is the GL-side view of the same gap and lives in the accounting reports.
