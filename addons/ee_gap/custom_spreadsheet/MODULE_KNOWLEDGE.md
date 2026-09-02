---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_spreadsheet
manifest_version: 19.0.0.2.0
---

# custom_spreadsheet

## Purpose
A workbook layer (`custom.spreadsheet.workbook`) for the platform that complements Odoo 19 CE's `spreadsheet` engine. The grid renderer remains delegated to CE — this module owns the metadata: tags, sharing, versioning, CSV import/export, "load records from any model" bulk-fill, and three AI helpers (Ask AI, formula suggestion, data-cleaning report) that flow through `custom.ai._recommend`.

Grid data is stored as JSON text in `data_json` (default `{"sheets":[{"name":"Sheet1","cells":{}}]}`); cells are keyed `"row_col"`. Every `data_json` write auto-snapshots the *previous* value into `custom.spreadsheet.version` (unless `spreadsheet_skip_versioning` context flag is set).

## Business Flow
- User creates a `custom.spreadsheet.workbook` with a name, optional description, tags, owner; starts with an empty Sheet1.
- User edits the grid (UI delegated to CE `spreadsheet`) — every save passes `data_json` through `write()` which: detects changes, snapshots the old value as the next `custom.spreadsheet.version` row (`version_no` = previous max + 1), then super-writes.
- `action_open_import_wizard()` launches `custom.spreadsheet.import.wizard` to parse a CSV file (≤10 000 rows) and replace the target sheet via `_apply_csv_rows`.
- `action_export_csv()` materialises sheet 0 of the workbook into a downloadable `ir.attachment` (CSV), attaches it to the workbook chatter, and returns an `act_url` to `/web/content/<id>?download=1`.
- `action_load_from_model(model_name, domain, fields_list, sheet_name, append)` (also via `custom.spreadsheet.load.wizard`) pulls up to 10 000 records from any model with a configurable domain + field list, writes them as a header row + data rows into the named sheet. `append=True` appends below existing data.
- `action_ask_ai(question)` → AI mode `ask`; result text posted to chatter.
- `action_ai_formula_suggest(cell_ref, context_text)` → AI mode `formula`; result stored on `suggested_formulas` + chatter.
- `action_ai_data_clean()` → AI mode `clean`; result stored on `ai_clean_report` + chatter.
- All three AI calls build a payload via `_custom_ai_payload(question, mode, extra)` that includes `data_summary` (per-sheet row/col/cell counts + 25 sample cells) and a truncated 4 000-char excerpt of `data_json`.
- `action_generate_share_token()` mints a token; `share_url` exposes `{base}/custom_spreadsheet/share/<token>` for read-only HTML render.
- `action_view_versions()` opens the version list; `custom.spreadsheet.version` rows expose a one-click restore.

## Key Models
- `custom.spreadsheet.workbook` — Main entity; inherits `mail.thread`, `pdp.audited.mixin`.
- `custom.spreadsheet.version` — Immutable snapshot row (`version_no`, `data_json_snapshot`, `saved_by`, `note`).
- `custom.spreadsheet.tag` — Free-form tag dictionary (M2m on workbook).
- `custom.spreadsheet.import.wizard` (TransientModel) — CSV importer.
- `custom.spreadsheet.load.wizard` (TransientModel) — Load-from-model bulk-fill.

## Important Fields
- `custom.spreadsheet.workbook.data_json` (Text, default `{"sheets":[…]}`) — full grid state.
- `custom.spreadsheet.workbook.owner_id` (M2o res.users, tracked).
- `custom.spreadsheet.workbook.shared_user_ids` (M2m res.users) — explicit shares (read access governed by record rules in security).
- `custom.spreadsheet.workbook.tag_ids` (M2m custom.spreadsheet.tag).
- `custom.spreadsheet.workbook.is_published` (Boolean).
- `custom.spreadsheet.workbook.share_token` (Char, indexed, copy=False) + `share_url` (Char, computed).
- `custom.spreadsheet.workbook.suggested_formulas` (Text, readonly) — last AI formula response.
- `custom.spreadsheet.workbook.ai_clean_report` (Text, readonly) — last AI cleaning response.
- `custom.spreadsheet.workbook.version_ids` (One2many) / `version_count` (Integer, computed).
- `custom.spreadsheet.version.version_no` (Integer, monotonic per workbook).
- `custom.spreadsheet.version.data_json_snapshot` (Text) — full previous grid.
- `custom.spreadsheet.version.saved_by` (M2o res.users) / `note` (Char).
- Constants: `_AI_PAYLOAD_MAX_CHARS=4000`, `_MAX_IMPORT_ROWS=10000`, `_MAX_LOAD_RECORDS=10000`.

## Public Methods
- `custom.spreadsheet.workbook.action_ask_ai(question=None)` — `mode="ask"` AI call.
- `custom.spreadsheet.workbook.action_ai_formula_suggest(cell_ref, context_text)` — `mode="formula"`.
- `custom.spreadsheet.workbook.action_ai_data_clean()` — `mode="clean"`.
- `custom.spreadsheet.workbook._custom_ai_payload(question, mode, extra)` — payload builder.
- `custom.spreadsheet.workbook._call_ai(payload)` — delegate to `custom.ai._recommend`.
- `custom.spreadsheet.workbook.action_export_csv()` — produces CSV `ir.attachment`, returns download URL.
- `custom.spreadsheet.workbook._apply_csv_rows(rows, sheet_name)` — used by import wizard.
- `custom.spreadsheet.workbook.action_load_from_model(model_name, domain, fields_list, sheet_name, append)` — bulk pull.
- `custom.spreadsheet.workbook._snapshot_version(data_json, note)` — internal versioning helper called from `write()`.
- `custom.spreadsheet.workbook.action_generate_share_token()` / `action_revoke_share_token()`.
- `custom.spreadsheet.workbook.action_view_versions()` / `action_open_import_wizard()` / `action_open_load_wizard()`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_documents`, `custom_ai_bridge`.
- **Inherits from:** `mail.thread`, `pdp.audited.mixin` on workbook.
- **Extended by:** none in tree; verticals can seed workbooks via XML or call `action_load_from_model` programmatically from a scheduler.
- **External calls:** `custom.ai._recommend` (via `custom_ai_bridge`).
- **Cross-vertical:** generic workbook surface. BRD requirements about "spreadsheet / Excel-like / data exploration / ad-hoc report" should map here.

## Gotchas
- **Snapshot-on-write inflates DB rapidly** — every grid save persists the entire previous `data_json` as a new row. Large workbooks × frequent saves = quickly bloated `custom_spreadsheet_version`. Use `with_context(spreadsheet_skip_versioning=True)` for bulk loads (the load wizard does not currently set this — see code).
- **AI payload truncates `data_json` to 4 000 chars** — for non-trivial workbooks the AI sees a tiny prefix only. `_data_summary` adds 25 sample cells per sheet; that's the entire window.
- **`action_load_from_model` uses `Model.sudo().search`** — bypasses record rules for the caller. Limit to admin role or vet `model_name` whitelist via record rules.
- **`_eval_domain` only accepts list-literal domains** (`ast.literal_eval`) — no field references, no `relativedelta`. Domain strings with expressions silently fall to "Domain must be a list literal" `UserError`.
- **CSV export only emits sheet 0** — no multi-sheet XLSX export.
- **CSV import REPLACES the target sheet** — there is no merge mode.
- **`share_token` portal page is read-only HTML** — not the CE spreadsheet renderer. Public viewers see a flat table, not a live grid.
- **`shared_user_ids` is declared but the actual ACL must be enforced via record rules in `security/`** — the field is data only.
- **Version restore is in the version model's action, not the workbook**, and the restore path must set `spreadsheet_skip_versioning=True` or it will create a snapshot of the restored value (acceptable but doubles writes).

## Out of Scope
- **Interactive grid rendering / formulas / pivot tables / charts** — delegated to CE `spreadsheet`.
- **XLSX import/export** — only CSV.
- **Real-time collaboration / multi-user cursors** — out of scope at this layer.
- **Per-cell ACL / range protection** — workbook-level access only.
- **Scheduled refresh of load-from-model data** — must be triggered manually or by an external cron.
- **AI-generated grid (write-side)** — AI returns text only; no auto-fill of cells.
- **Cell-level formulas evaluated server-side** — formulas are stored as cell values; evaluation is client-side via CE engine.
