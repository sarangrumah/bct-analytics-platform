# Custom Coretax (Indonesia DJP)

Implements the Coretax DJP compliance surface aligned with PER-11/PJ/2025
(effective 22 May 2025): NSFP lifecycle, e-Faktur and Bukti Potong XML
export/import, Sertifikat Elektronik storage, and an adapter abstraction
ready for future H2H ASPP integration.

## Models

- `custom.coretax.config` — per-company DJP config: NPWP, KPP, address,
  Sertel attachment ref, adapter type.
- `account.move` extension — adds `nsfp` (17-digit) and
  `coretax_status` (`draft` / `submitted` / `approved` / `rejected_djp`).
  NSFP is empty on draft and assigned by DJP after portal approval.
- `custom.coretax.bukti.potong` — header record for incoming /
  outgoing Bukti Potong (PPh 21 / 23 / 26 / Unifikasi). Reconciles to
  `account.move.line` PPh receivable.
- `custom.coretax.adapter.base` — abstract H2H adapter. Methods:
  `submit_xml(xml)`, `query_status(uuid)`, `download_response(uuid)`.
  Default `ManualAdapter` is a no-op (operators paste NSFP from the
  portal manually).

## Wizards

- "Export e-Faktur / Bupot" (`wizards/coretax_export_wizard_views.xml`)
  — pick period + document type → generate validated XML batch.
- "Import Faktur / Bupot" (`wizards/coretax_import_wizard_views.xml`)
  — upload XML, parse, match to `res.partner` by NPWP/NIK, create
  Bukti Potong records.
- "Upload Sertel" (`wizards/coretax_sertel_upload_views.xml`) — upload
  `.p12`, encrypt at rest with Fernet via `custom.ir.config`. Master
  key from env `CORETAX_SERTEL_MASTER_KEY`.

## XSD Validation (Optional)

XML payloads can be validated against XSDs at
`data/xsd/<document_type>.xsd` before submission using the `xmlschema`
Python library. **DJP does not publish Coretax XSDs publicly** —
empirically confirmed (as of May 2026) that pajak.go.id ships Excel
converter templates, sample XML payloads (in ZIPs), and a Windows
converter binary, but no raw `.xsd` files. Client-side validation is
therefore optional:

- **Without XSDs (default):** the wizard logs a warning and exports
  XML anyway. DJP performs authoritative server-side validation on
  Coretax portal upload.
- **With XSDs (advanced):** if your organisation has obtained XSDs
  through an ASPP subscription (Pajakku, OnlinePajak, Klikpajak) or
  a direct B2B agreement with DJP, drop them at
  `data/xsd/<document_type>.xsd` to enable pre-submission validation.

See `data/xsd/README.md` for expected filenames per document type.

## Document Types Covered

- e-Faktur Keluaran, Faktur Masukan
- Bupot PPh 21 (Pegawai Tetap & Bukan Pegawai Tetap)
- Bupot PPh 23
- Bupot PPh 26
- Bupot Unifikasi

## Audit

Every export / import / sertel access is written to `pdp.audit_log`
with `action='xml_export'` / `'xml_import'` / `'sertel_access'`
respectively.

## Security Groups

- `coretax.group_admin` — manage config, upload sertel.
- `coretax.group_user` — run export / import wizards, view records.

## Dependencies

- `custom_core`, `account`
- Python: `lxml`, `xmlschema`, `cryptography`

## Install

1. Set `CORETAX_SERTEL_MASTER_KEY` (32-byte Fernet key, base64) in
   `.env` before first boot.
2. (Optional) If your organisation has DJP XSDs from a private
   channel, place them under `data/xsd/<document_type>.xsd` — see
   `data/xsd/README.md`. Skip otherwise; module works without.
3. Install via Apps menu.
4. Configure NPWP / KPP / Sertel under Settings → Custom Platform →
   Coretax.

## Notes

A public REST API for DJP Coretax B2B integration is not confirmed as
of May 2026. The default workflow uses XML upload through the official
Coretax portal. The adapter abstraction is ready for a host-to-host
adapter when an ASPP subscription is in place.

## Reference

- `docs/coretax.md`
- PER-11/PJ/2025 (NSFP format)
