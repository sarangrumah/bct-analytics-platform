# -*- coding: utf-8 -*-
{
    "name": "Custom Studio Lite",
    "summary": "Declarative custom-field + view-extension manager (lightweight Studio replacement)",
    "description": """
Lightweight 'Studio': admins declare custom fields + view inserts via
DB records (rather than editing source). The manager pre-creates
``ir.model.fields`` rows (prefixed ``x_studio_``) and ``ir.ui.view``
inheritances on save. Useful for vertical-specific quick tweaks
without forking a module.
""",
    "author": "Custom Platform",
    "category": "Productivity/Studio",
    "version": "19.0.0.6.3",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "base",
        "base_automation",
        "custom_whatsapp",
    ],
    "capability_tags": ["audit-trail", "pdp"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/studio_view_customization_views.xml",
        "views/studio_custom_field_views.xml",
        "views/studio_automation_rule_views.xml",
        "views/studio_report_customization_views.xml",
        "views/studio_view_editor_action.xml",
        "views/menu_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            # Service first (registry consumer order doesn't matter for
            # runtime, but explicit ordering keeps the bundle tidy).
            "custom_studio_lite/static/src/js/studio_overlay/studio_overlay_service.js",
            "custom_studio_lite/static/src/js/studio_view_editor/studio_view_editor.js",
            "custom_studio_lite/static/src/js/studio_view_editor/studio_view_editor.xml",
            "custom_studio_lite/static/src/js/studio_view_editor/studio_view_editor.scss",
            "custom_studio_lite/static/src/js/studio_systray/studio_systray.js",
            "custom_studio_lite/static/src/js/studio_systray/studio_systray.xml",
            "custom_studio_lite/static/src/js/studio_systray/studio_systray.scss",
            "custom_studio_lite/static/src/js/studio_overlay/studio_overlay.js",
            "custom_studio_lite/static/src/js/studio_overlay/studio_overlay.xml",
            "custom_studio_lite/static/src/js/studio_overlay/studio_overlay.scss",
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
}
