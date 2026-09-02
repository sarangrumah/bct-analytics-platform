# -*- coding: utf-8 -*-
"""Post-install hook: link optional sibling-module menus.

Optional menus in ``views/menu_views.xml`` are declared *without* an
``action`` attribute so they load even when the target action does not
exist. This hook resolves each pair (menu xmlid, action xmlid) and:

* sets ``menu.action`` when the action is present in the registry
* deactivates the menu (``active=False``) when the action is missing

This way ``custom_hub_console`` installs cleanly on a tenant where only
a subset of sibling modules (``custom_ops_monitor``,
``custom_brd_analyzer``, ``custom_hht_bridge``) is installed.
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)


# (hub menu xmlid, target action xmlid)
_OPTIONAL_MENU_LINKS = [
    ("custom_hub_console.menu_hub_monitoring", "custom_ops_monitor.action_health_dashboard"),
    ("custom_hub_console.menu_hub_tenant_health", "custom_ops_monitor.action_tenant_health"),
    ("custom_hub_console.menu_hub_capacity", "custom_ops_monitor.action_capacity_forecast"),
    ("custom_hub_console.menu_hub_incidents", "custom_ops_monitor.action_ops_incident"),
    ("custom_hub_console.menu_hub_brd_documents", "custom_brd_analyzer.action_brd_document"),
    ("custom_hub_console.menu_hub_brd_recommendations", "custom_brd_analyzer.action_brd_recommendation"),
    ("custom_hub_console.menu_hub_brd_capability", "custom_brd_analyzer.action_capability_entry"),
    ("custom_hub_console.menu_hub_hht_devices", "custom_hht_bridge.action_hht_device"),
    ("custom_hub_console.menu_hub_ai_anomaly", "custom_ai_features.action_anomaly_finding"),
    ("custom_hub_console.menu_hub_ai_nlq", "custom_ai_features.action_nlq_session"),
]


def _post_install_link_menus(env):
    """Resolve optional menu->action references after install."""
    IrData = env["ir.model.data"]
    Menu = env["ir.ui.menu"]
    linked, hidden = 0, 0
    for menu_xmlid, action_xmlid in _OPTIONAL_MENU_LINKS:
        try:
            menu_module, menu_name = menu_xmlid.split(".", 1)
            menu_rec = IrData._xmlid_to_res_id(menu_xmlid, raise_if_not_found=False)
            if not menu_rec:
                continue
            menu = Menu.browse(menu_rec)
            action_id = IrData._xmlid_to_res_id(action_xmlid, raise_if_not_found=False)
            if action_id:
                a_module, a_name = action_xmlid.split(".", 1)
                # Fetch the action record to get its model+id "ir.actions.act_window,42"
                a_rec = IrData.search([("module", "=", a_module), ("name", "=", a_name)], limit=1)
                if a_rec:
                    menu.action = f"{a_rec.model},{action_id}"
                    linked += 1
                else:
                    menu.active = False
                    hidden += 1
            else:
                menu.active = False
                hidden += 1
        except Exception as exc:  # pragma: no cover - defensive
            _logger.warning(
                "[hub_console] post-install link failed for %s -> %s: %s",
                menu_xmlid,
                action_xmlid,
                exc,
            )
    _logger.info(
        "[hub_console] post-install: %s optional menus linked, %s hidden",
        linked,
        hidden,
    )

    # Seed module catalog so the Hub Admin "Module Deployments" page is
    # not empty on first login. Scanner is idempotent and never deletes.
    try:
        out = env["custom.hub.module.catalog"].sudo()._action_scan_all()
        _logger.info(
            "[hub_console] post-install catalog seed: created=%s updated=%s total=%s",
            out.get("created"),
            out.get("updated"),
            out.get("total"),
        )
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning("[hub_console] post-install catalog seed failed: %s", exc)
