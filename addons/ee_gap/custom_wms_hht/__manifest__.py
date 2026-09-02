# -*- coding: utf-8 -*-
# License: LGPL-3
{
    "name": "Custom WMS Handheld",
    "summary": "Task-driven handheld UI for the WMS stack: sidebar shell, package scan, receive/putaway/pick/pack/count/bin-to-bin",
    "description": """
WMS Handheld (HHT) Application
==============================
Replaces the generic ``custom_hht_bridge`` demo shell (5 tabs, empty stubs, a
scan endpoint that only *logged* the scan) with a task-driven warehouse app
that actually moves stock through the WMS modules:

- **Sidebar shell** with a work-queue badge per module instead of flat tabs.
- **Receive** — open receipts, GS1/EAN scan, IMEI serial capture, expiry +
  supplier batch, QC pass/fail on the quarantine gate.
- **Putaway** — the engine's ranked bin suggestion per line, accept or
  override by scanning a bin.
- **Pick & Pack** — pick list grouped by source bin, scan-to-confirm, put in
  package, validate.
- **Package** — scan any package to see its contents, location and history;
  move it bin-to-bin.
- **Count** — cycle-count / spot-check sessions line by line.
- **Bin-to-bin** — transfer-order proposals raised by the low-water engine.
- **Stock check** — scan a product to see its details, the put-away
  engine's suggested bin, and on-hand/reserved stock per bin. Read-only.

Deliberately a separate module from ``custom_hht_bridge``: the bridge is
installed on the tenant production databases that have none of the ``custom_wms_*``
models, and it must not be forced to upgrade for a WMS-only feature.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Inventory/Mobile",
    "version": "19.0.0.4.0",
    "license": "LGPL-3",
    "depends": [
        "custom_hht_bridge",
        "custom_barcode",
        "custom_product_barcode",
        "custom_wms_putaway",
        "custom_wms_inbound_qc",
        "custom_wms_cycle_count",
        "custom_wms_to_engine",
        "custom_wms_receiving_ext",
        "stock",
    ],
    "capability_tags": ["hht", "wms", "barcode-scan", "goods-receipt", "picking"],
    "data": [
        "views/hht_shell_layout_views.xml",
    ],
    "assets": {
        "custom_wms_hht.pwa_assets": [
            # Same base as the bridge shell — see custom_hht_bridge/__manifest__
            # for why each of these is load-bearing on old Android WebViews.
            ("include", "web._assets_helpers"),
            ("include", "web._assets_backend_helpers"),
            "web/static/src/scss/pre_variables.scss",
            "web/static/lib/bootstrap/scss/_variables.scss",
            "web/static/lib/bootstrap/scss/_variables-dark.scss",
            "web/static/lib/bootstrap/scss/_maps.scss",
            ("include", "web._assets_bootstrap_backend"),
            ("include", "web._assets_core"),
            ("remove", "web/static/src/core/debug/**/*"),
            "web/static/src/polyfills/**/*.js",
            "web/static/lib/odoo_ui_icons/*",
            "web/static/src/libs/fontawesome/css/font-awesome.css",
            # Reused verbatim from the bridge: HMAC signing + offline queue.
            "custom_hht_bridge/static/src/js/hht_shell/crypto.js",
            "custom_hht_bridge/static/src/js/hht_shell/sync_queue.js",
            # This app.
            "custom_wms_hht/static/src/scss/wms_hht.scss",
            "custom_wms_hht/static/src/js/wms_hht/rpc.js",
            "custom_wms_hht/static/src/js/wms_hht/scanBurst.js",
            "custom_wms_hht/static/src/js/wms_hht/pickingScan.js",
            "custom_wms_hht/static/src/js/wms_hht/pages/ReceivePage.js",
            "custom_wms_hht/static/src/js/wms_hht/pages/PutawayPage.js",
            "custom_wms_hht/static/src/js/wms_hht/pages/PickPage.js",
            "custom_wms_hht/static/src/js/wms_hht/pages/PackagePage.js",
            "custom_wms_hht/static/src/js/wms_hht/pages/CountPage.js",
            "custom_wms_hht/static/src/js/wms_hht/pages/BinToBinPage.js",
            "custom_wms_hht/static/src/js/wms_hht/pages/StockPage.js",
            "custom_wms_hht/static/src/js/wms_hht/pages/pages.xml",
            "custom_wms_hht/static/src/js/wms_hht/wms_shell.js",
            "custom_wms_hht/static/src/js/wms_hht/wms_shell.xml",
            "custom_wms_hht/static/src/js/wms_hht/main.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
