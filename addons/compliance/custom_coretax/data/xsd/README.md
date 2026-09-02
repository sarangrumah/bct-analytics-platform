# Coretax XSD Drop Zone (Operator-Managed)

This directory is intentionally empty in the repository. It exists as
a documented location where operators can place DJP Coretax XSD files
to enable client-side pre-submission validation in the
`Export e-Faktur / Bupot` wizard
(`wizards/coretax_export_wizard.py`).

## Expected filenames

The wizard looks for `data/xsd/<document_type>.xsd` where
`<document_type>` matches one of the codes in `DOCUMENT_TYPES`:

| Filename                       | Document Type                          |
|--------------------------------|----------------------------------------|
| `efaktur_keluaran.xsd`         | e-Faktur Keluaran (Output VAT)         |
| `faktur_masukan.xsd`           | Faktur Masukan (Input VAT)             |
| `bupot_21_tetap.xsd`           | Bupot PPh 21 — Pegawai Tetap           |
| `bupot_21_bukan_tetap.xsd`     | Bupot PPh 21 — Bukan Pegawai Tetap     |
| `bupot_23.xsd`                 | Bupot PPh 23                           |
| `bupot_26.xsd`                 | Bupot PPh 26                           |
| `bupot_unifikasi.xsd`          | Bupot Unifikasi                        |

## Why this directory is empty

**DJP does not publish Coretax XSDs publicly.** As of May 2026, the
official Coretax materials at
<https://www.pajak.go.id/reformdjp/coretax/template-xml-dan-converter-excel-ke-xml>
consist of:

- Excel converter templates (`.xlsx`) — operators fill these in and a
  converter tool produces XML
- Sample XML payloads bundled in ZIPs (`bpmp.zip`, `bp21.zip`,
  `bp23.zip`, etc.) — each ZIP contains one example `.xml` and
  nothing else
- A Windows-only converter binary (`ConverterEfakturCoretax__v1.6.zip`,
  ~1.8MB) containing a `.NET .exe` plus sample XMLs and Excel
  templates

None of the above contains `.xsd` schema files (verified empirically
by enumerating `bpmp.zip`, `bp21.zip`, and the converter binary —
none contained any file with `.xsd` extension).

## How to obtain XSDs

XSDs can typically be obtained through:

- **ASPP subscription** — Application Service Providers for Pajak
  (e.g. Pajakku, OnlinePajak, Klikpajak) receive Coretax schema
  specifications as part of their host-to-host integration contracts
  with DJP. An organisational ASPP subscription is usually the
  fastest path.
- **Direct B2B agreement with DJP / Direktorat Teknologi Informasi
  Perpajakan** — for organisations with sufficient submission volume
  to warrant a dedicated host-to-host integration.

## What happens if this directory stays empty

The wizard still generates and exports XML normally. The local
validation step is skipped and a `validation_warning` field is
populated on the wizard result for operator visibility. The XML
file is then uploaded via the official DJP Coretax portal, which
performs authoritative server-side validation. Rejection (if any)
is communicated via DJP's portal feedback.

In short: missing XSDs do not block submission — they only remove
the convenience of catching malformed XML before upload.

## Namespace alignment (required when XSDs are added)

Once XSDs are placed here, also align `NS_CORETAX` in
`wizards/coretax_export_wizard.py:47` with the actual
`targetNamespace` declared in each XSD. The current placeholder
`urn:djp:coretax:v1` will otherwise trigger namespace-mismatch
validation failures even when the XML structure is correct.

## Repository hygiene

Do not commit downloaded XSDs to this repository unless your
organisation has explicit redistribution rights from DJP / your
ASPP. The recommended pattern is to keep this directory empty in
git and provision XSDs out-of-band (e.g. via the deployment image
build pipeline, or a one-time post-install operator step).
