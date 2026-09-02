# -*- coding: utf-8 -*-
{
    "name": "Custom Retail Import",
    "summary": "Excel/CSV + SFTP ingestion adapter for retail master & transaction data",
    "description": """
Custom Retail Import
====================

Productized adapter that converts customer-provided Excel/CSV files into Odoo
records, plus an optional direct-from-SFTP recurring feed. Built for the apparel brand
(PT Sinar Eka Selaras) onboarding and reusable for any retail tenant.

Generalizes the ``custom_bank_import`` template/wizard/log trio into per-file-type
**import profiles** (X101 products, X20 on-hand, X24 POS sales, X70 tenders, CoA,
company, store master), and ports the proven era_busana_retailindo X101 pipeline
into the X101 executor.

Pieces
------
* ``retail.import.profile`` — declares how to parse one file type (xlsx/csv,
  ``data_start_row`` for report-style files with a title row, a JSON column map,
  date/decimal parsing, U+FFFD→® cleanup).
* ``retail.import.wizard`` — Binary upload with a **dry-run preview**; SHA256
  file-hash dedup; large files (X101) run async via ``queue_job`` to dodge the
  reverse-proxy timeout.
* ``retail.import.executor`` — per-file-type loaders with ``ir.model.data``
  external IDs for idempotency. X101/CoA/company/X20 are full; X24/X70 are
  Phase-5 decision-gated; X70T/X31/X32P/store-master are staged.
* ``retail.import.log`` — audit trail, keeps the source file in ir.attachment.
* ``retail.import.feed`` — SFTP poller (paramiko) that pulls new files into the
  same executor on an ``ir.cron`` schedule.
* ``retail.import.mailbox`` — IMAP bridge for tenants whose source files arrive as
  nightly email attachments. Backs every attachment up to the SFTP share, drops the
  ingestible ones into a feed's directory, then deletes the message once the backup
  verifies. Deletion keys off the backup, never the import, so a parser bug can never
  destroy source data.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Inventory/Retail",
    "version": "19.0.0.22.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_product_barcode",
        "queue_job",
        "product",
        "stock",
        "account",
    ],
    "external_dependencies": {"python": ["openpyxl"]},
    "capability_tags": ["data-import", "retail", "excel", "sftp", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/cron.xml",
        "data/retail_import_profiles.xml",
        "data/feeds.xml",
        "data/mailboxes.xml",
        "views/retail_import_profile_views.xml",
        "views/retail_import_log_views.xml",
        "views/retail_import_feed_views.xml",
        "views/retail_import_mailbox_views.xml",
        "views/retail_mdm_pending_sku_views.xml",
        "wizard/retail_import_wizard_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
