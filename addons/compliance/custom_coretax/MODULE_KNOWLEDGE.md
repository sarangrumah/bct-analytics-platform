---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_coretax
manifest_version: 19.0.0.4.0
---

# custom_coretax

## Purpose
Implements the **Indonesian Coretax DJP compliance surface** (PER-11/PJ/2025) for the Custom Odoo platform: NSFP (Nomor Seri Faktur Pajak) lifecycle on `account.move`, XML export/import wizards for the 7 main document types (e-Faktur Keluaran/Masukan, Bupot PPh 21 Tetap/Bukan Tetap, Bupot 23/26/Unifikasi), Bukti Potong receipt records, encrypted Sertifikat Elektronik (.p12) storage, and a pluggable adapter abstraction so that future host-to-host (ASPP) backends can replace the default manual portal-upload flow.

Every export, import, and sertel access emits an audit row to `pdp.audit_log` (`xml_export` / `xml_import` / `sertel_access`).

## Business Flow
- Tenant admin creates a `custom.coretax.config` (NPWP digits-only, KPP code, taxpayer name/address) and runs the `custom.coretax.sertel.upload.wizard` to upload the `.p12`. The wizard validates the file via `cryptography.hazmat.primitives.serialization.pkcs12` (if installed), encrypts via `custom.ir.config.set_encrypted` (env-keyed Fernet), and persists the ciphertext at `ir.config_parameter` key `coretax.sertel.<config_id>`. The password is **never** persisted; `sertel_access` audit row is written.
- Operator opens `custom.coretax.export.wizard`, picks a `document_type` and a year/month range, runs `action_generate_xml`:
  1. `_gather_records` selects posted `account.move`s (for VAT docs) or `custom.coretax.bukti.potong` rows (for bupot).
  2. `_build_xml` constructs the tree with `lxml.etree`, `NS_CORETAX = "urn:djp:coretax:v1"` (placeholder; operator must align with official targetNamespace once XSDs are present).
  3. `_validate_xml` checks `addons/.../data/xsd/<doc_type>.xsd`; if missing, emits a warning string; if present, calls `xmlschema.XMLSchema(...).validate()`.
  4. Persists `ir.attachment`, sets `xml_file` for download, writes `xml_export` audit row.
- Operator uploads the XML to the official Coretax portal (manual adapter), then runs `custom.coretax.import.wizard` to ingest the DJP response XML:
  - For bupot docs: creates `custom.coretax.bukti.potong` rows, partner-matched on `vat` digits-only (with `vat ilike npwp[:9]` fallback), tolerant of namespaced and unnamespaced XML, dedupes on `(no_bupot, source)`.
  - For VAT docs: locates the `account.move` by `name`, writes `x_custom_nsfp` (if 17 digits) and bumps `x_custom_coretax_status` to `approved` or `submitted`.
- `custom.coretax.adapter.base._get_for_config(config)` dispatches by `config.adapter_type`: `manual` → `custom.coretax.adapter.manual` (returns `manual_required`, no NSFP, raises on `download_response`); `h2h_aspp` → must be installed separately.
- `account.move.x_custom_coretax_status` workflow: `draft → submitted → approved | rejected_djp`. Constraint `_check_nsfp_required_on_approval` blocks `approved` without a 17-digit NSFP. NSFP format `_NSFP_RE = ^\d{17}$` (2 transaction-code + 2 status-code + 13 serial).

## Key Models
- `custom.coretax.config` — Per-tenant taxpayer identity + sertel pointer + adapter selection. Stored model (not `res.config.settings`) so sertel/credential survive settings rewrites.
- `custom.coretax.bukti.potong` — Bukti Potong record (received/issued). Unique on `(no_bupot, source)`. Inherits `mail.thread`/`mail.activity.mixin`.
- `custom.coretax.adapter.base` (AbstractModel) — Adapter contract: `submit_xml(bytes) -> {submission_uuid, status, message}`, `query_nsfp(uuid)`, `download_response(uuid)`.
- `custom.coretax.adapter.manual` (AbstractModel) — Default no-op adapter returning `manual_required`.
- `custom.coretax.export.wizard` / `custom.coretax.import.wizard` / `custom.coretax.sertel.upload.wizard` (TransientModels).
- `account.move` (inherited) — adds NSFP + Coretax status fields.

## Important Fields
- `custom.coretax.config.npwp` (Char size=16) — 15 or 16 digits only; constraint `_NPWP_DIGITS_RE = ^\d{15,16}$`; unique.
- `custom.coretax.config.kpp_code` (Char size=3) — must be 3 digits.
- `custom.coretax.config.adapter_type` (Selection: manual/h2h_aspp) — dispatch key.
- `custom.coretax.config.sertel_uploaded` (Boolean, computed) — derived from `custom.ir.config.get_encrypted` truthiness.
- `custom.coretax.config.aspp_credential_key` (Char) — ir.config_parameter key pointer; plaintext never stored on the record.
- `account.move.x_custom_nsfp` (Char size=17, tracked) — 17 digits assigned by DJP after Coretax approval. Format `TT + SS + YYNNNNNNNNNNN`.
- `account.move.x_custom_coretax_status` (Selection: draft/submitted/approved/rejected_djp, tracked) — independent of accounting state.
- `account.move.x_custom_coretax_status_code` (Selection: `00`..`09`) — faktur status / pengganti code.
- `account.move.x_custom_coretax_submission_uuid` (Char) — reference from portal/ASPP.
- `account.move.x_custom_coretax_response_attach_id` (M2o `ir.attachment`) — approval PDF/XML.
- `custom.coretax.bukti.potong.jenis_pph` (Selection: 21/23/26/4_2/15/22, indexed) — PPh kind.
- `custom.coretax.bukti.potong.source` (Selection: received/issued, indexed) — perspective; uniqueness scoped per source.
- `custom.coretax.bukti.potong.state` (Selection: draft/confirmed/exported/submitted/approved/cancelled, tracked).
- `custom.coretax.bukti.potong.period_year` / `period_month` (Integer, indexed) — constrained `1≤month≤12`, `2000≤year≤2100`.

## Public Methods
- `custom.coretax.config._get_active()` (`@api.model`) — returns the active config or raises.
- `custom.coretax.config.get_sertel_p12()` — decrypts and returns raw .p12 bytes (or None).
- `custom.coretax.adapter.base.submit_xml/query_nsfp/download_response` — abstract.
- `custom.coretax.adapter.base._get_for_config(config)` (`@api.model`) — adapter dispatcher; raises `UserError` if not installed.
- `custom.coretax.export.wizard.action_generate_xml()` — full export pipeline; returns an `ir.actions.act_url` download.
- `custom.coretax.export.wizard._gather_records()` / `_period_domain(date_field)` / `_build_xml(records)` / `_validate_xml(xml_bytes)` / `_audit_log_export(filename, count)`.
- `custom.coretax.import.wizard.action_import()` / `_import_bupot(root)` / `_import_invoices(root)` / `_audit_log_import(created, skipped)`.
- `custom.coretax.sertel.upload.wizard.action_store()` — pkcs12-validate, encrypt, persist; scrubs the password field on return.
- `custom.coretax.bukti.potong.action_confirm/action_cancel/action_draft`.

## Integration Points
- **Depends on:** `custom_core`, `account`, `mail`. Optional runtime: `lxml`, `xmlschema`, `cryptography`.
- **Inherits from:** `account.move` (NSFP/status fields), `mail.thread` + `mail.activity.mixin` (on bukti.potong).
- **Extended by:** `custom_coretax_bupot` (Bupot Unifikasi v2), `custom_pph_witholding` (engine that feeds Bupot lines), a future `custom.coretax.adapter.h2h_aspp` host-to-host module.
- **External calls:** none by default; DJP Coretax B2B REST is not officially documented as of May 2026, so the default adapter is manual portal upload.
- **Cross-vertical:** generic Indonesian tax compliance.

## Gotchas
- **XSDs are NOT bundled and not publicly available.** As of May 2026, DJP does not publish raw `.xsd` files at pajak.go.id — the converter page ships only Excel templates, sample XMLs (in ZIPs like `bpmp.zip`, `bp21.zip`), and a Windows converter binary. Operators with XSDs from a private channel (ASPP subscription, Pajakku partner contract, etc.) can drop them under `data/xsd/<document_type>.xsd` for client-side validation; without them the wizard exports XML and logs a `validation_warning` (DJP still validates server-side on portal upload). See `data/xsd/README.md` for filenames + obtaining channels.
- **`NS_CORETAX = "urn:djp:coretax:v1"` is a placeholder.** Once official XSDs are placed, the operator may need to align the `targetNamespace`; mismatched namespaces will cause `xmlschema` validation failures.
- **PMK131 tax + fiscal-position data is gated out of the default `data` list** (commented in `__manifest__.py`) because it requires `custom_accounting_full` to have provisioned PSAK account types first. Load manually with `-i custom_coretax --without-demo=False` after that module is set up.
- **Sertel password is transient by design.** If the operator loses the password, the .p12 ciphertext is unrecoverable; re-upload required. There is no key-escrow.
- **`get_sertel_p12()` returns `None` on malformed ciphertext** and logs an ERROR — callers must defensively check, not assume bytes.
- **Manual adapter `download_response` raises `UserError`** rather than returning empty bytes; callers must guard.
- **Import wizard partner matching falls back to `vat ilike npwp[:9]`** — this is permissive and may match the wrong partner if multiple partners share an NPWP prefix.
- **Period domain is half-open `[from, to+1month)`** but the "to" boundary uses `date(end_year, end_month+1, 1)` without month-wrap handling beyond the `if end_month == 12` branch — careful when picking `month_to=12` across year boundary in the same wizard run (the wizard requires `year_to`).
- **Bupot `_no_bupot_unique_per_source` allows the same `no_bupot` to exist twice if one is `received` and one is `issued`** — by design, but be aware in reporting.
- **`_audit_log_*` uses raw SQL INSERT into `pdp.audit_log`** — `custom_pdp_audit` must be installed and its schema bootstrapped, otherwise the wizard succeeds but the audit row is missing (an `ERROR` log is written).
- **This module's groups sit on its own `custom_coretax.res_groups_privilege_coretax` privilege** ("Coretax") — Odoo 19 renders every group sharing one `res.groups.privilege` as a single pick-one dropdown on the user form, so a privilege shared across modules makes saving a user silently drop the other modules' groups. The privilege carries `custom_core.module_category_custom_platform`, so the selector still appears alongside the other custom modules. Do not point new groups at `custom_core.res_groups_privilege_custom_platform`.

## Out of Scope
- **Real-time host-to-host submission to DJP** — manual portal upload is the only built-in adapter. The `h2h_aspp` adapter must be implemented in a downstream module (e.g. Pajakku ASPP integration referenced in the platform's Pajakku ASPP scope note).
- **NSFP allocation/quota management** — NSFP is assigned by DJP; this module just stores it.
- **e-Faktur lampiran (line-level data)** — only header fields are exported (NSFP, dates, partner, DPP, PPN, total). Item lines are not marshalled into the XML.
- **DJP response parsing beyond NSFP + status** — the import wizard sets NSFP and bumps status; richer error payloads must be added per real-world XSD.
- **Bupot PPh 21 Tetap/Bukan Tetap rich data** — the wizard handles them as bupot generally; specialised PPh21 calculator is in `custom_pph_witholding` (and HR modules), not here.
- **NPWP digital signature on XML** — sertel is stored, but the XML built by this module is NOT yet signed; signing must be added before host-to-host submission.
