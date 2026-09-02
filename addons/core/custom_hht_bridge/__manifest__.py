# -*- coding: utf-8 -*-
# License: LGPL-3
{
    "name": "Custom HHT Bridge",
    "summary": "Handheld Terminal bridge: PWA shell + secure REST API + offline sync (Zebra/DataWedge)",
    "description": """
Handheld Terminal Integration Bridge
====================================
Brings physical handheld scanners (Zebra TC-series, Honeywell, generic Android
with DataWedge / keyboard wedge) into the Odoo platform.

Provides:

- ``hht.device``: enrolled device registry with HMAC api_key/secret + CIDR whitelist.
- ``hht.scan.log``: append-only audit log of every scan with GPS + signature.
- ``hht.sync.queue``: FIFO journal of events queued by the PWA while offline.
- PWA shell mounted at ``/hht/`` (manifest.webmanifest, Service Worker, OWL app).
- REST API ``/api/hht/*`` guarded by ``@secure_endpoint('hht')`` (HMAC-SHA256 +
  timestamp drift + nonce replay + CIDR allow-list).
- DataWedge ingest endpoint for thin scanners.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Inventory/Mobile",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_bast",
        "custom_barcode",
        "custom_super_admin",
        "stock",
        "mail",
        "web",
    ],
    "capability_tags": ["hht", "barcode-scan", "wms", "audit-trail", "multi-tenant"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_config_parameter_data.xml",
        "data/cron.xml",
        "views/hht_device_views.xml",
        "views/hht_scan_log_views.xml",
        "views/hht_sync_queue_views.xml",
        "views/hht_shell_layout_views.xml",
        "wizards/regenerate_secret_wizard_views.xml",
        "views/menu_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "custom_hht_bridge/static/src/scss/hht_shell.scss",
        ],
        "custom_hht_bridge.pwa_assets": [
            # SCSS variable/mixin helpers (no JS).
            ("include", "web._assets_helpers"),
            ("include", "web._assets_backend_helpers"),
            "web/static/src/scss/pre_variables.scss",
            "web/static/lib/bootstrap/scss/_variables.scss",
            "web/static/lib/bootstrap/scss/_variables-dark.scss",
            "web/static/lib/bootstrap/scss/_maps.scss",
            ("include", "web._assets_bootstrap_backend"),
            # The JS runtime: module loader (defines the `odoo` global), OWL,
            # registry, env/session. Without this the bundle's first line
            # throws "odoo is not defined" and nothing mounts.
            ("include", "web._assets_core"),
            ("remove", "web/static/src/core/debug/**/*"),
            # Required, not optional: core/utils/indexed_db.js calls
            # Set.prototype.difference, which only exists from Chrome 122
            # (02/2024). web._assets_core omits these, while web.assets_backend
            # pulls them in — so a bundle built on _assets_core alone ships the
            # caller without the polyfill and dies on any older WebView. Real
            # symptom: Chrome 119 on an Android 10 handheld threw
            # "this._tables.difference is not a function" and rendered nothing.
            "web/static/src/polyfills/**/*.js",
            "web/static/lib/odoo_ui_icons/*",
            "web/static/src/libs/fontawesome/css/font-awesome.css",
            "custom_hht_bridge/static/src/scss/hht_shell.scss",
            "custom_hht_bridge/static/src/js/hht_shell/crypto.js",
            "custom_hht_bridge/static/src/js/hht_shell/sync_queue.js",
            "custom_hht_bridge/static/src/js/hht_shell/pages/ReceivePage.js",
            "custom_hht_bridge/static/src/js/hht_shell/pages/IssuePage.js",
            "custom_hht_bridge/static/src/js/hht_shell/pages/TransferPage.js",
            "custom_hht_bridge/static/src/js/hht_shell/pages/CountPage.js",
            "custom_hht_bridge/static/src/js/hht_shell/pages/HandoverPage.js",
            "custom_hht_bridge/static/src/js/hht_shell/pages/pages.xml",
            "custom_hht_bridge/static/src/js/hht_shell/hht_shell.js",
            "custom_hht_bridge/static/src/js/hht_shell/hht_shell.xml",
            "custom_hht_bridge/static/src/js/hht_shell/main.js",
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
}
