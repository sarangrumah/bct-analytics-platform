# -*- coding: utf-8 -*-
{
    "name": "Custom Home Console",
    "summary": "Spotlight-style home landing: grouped app cards, search, shortcuts, branding",
    "description": """
Custom Home Console
===================

Replaces Odoo's default post-login landing with a richer surface for all
tenant users:

* Spotlight-style search (delegates to the built-in command palette).
* App cards grouped by category with per-tenant branding (logo + accent).
* Recently used apps (client cache) + user-pinned shortcuts (server).
* Tenant announcement banner, getting-started checklist, quick-create FAB.

Implementation notes
--------------------
* The landing is an ``ir.actions.client`` (tag ``custom_home_console.home``).
* The module sets this action as the **default home action** for users via
  ``res.users.action_id`` so it is reached by clicking the Odoo logo or by
  logging in fresh. Works on Community and Enterprise.
* On Enterprise, the OWL component also registers in ``main_components`` to
  replace the ``HomeMenu`` app grid; on Community the registry slot is
  simply absent and the patch is a no-op.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Custom Platform/UX",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "web",
        "custom_core",
        "mail",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/home_console_action.xml",
        "views/res_company_views.xml",
        "views/res_users_views.xml",
        "views/menu_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "custom_home_console/static/src/home_console/home_console.scss",
            "custom_home_console/static/src/home_console/pinned_shortcut_service.js",
            "custom_home_console/static/src/home_console/spotlight_search.js",
            "custom_home_console/static/src/home_console/spotlight_search.xml",
            "custom_home_console/static/src/home_console/home_console.js",
            "custom_home_console/static/src/home_console/home_console.xml",
            "custom_home_console/static/src/home_console/navbar_patch.js",
            "custom_home_console/static/src/home_console/navbar_apps_patch.xml",
        ],
    },
    "post_init_hook": "_post_init_set_default_home",
    "uninstall_hook": "_uninstall_clear_default_home",
    "installable": True,
    "application": False,
    "auto_install": False,
}
