# -*- coding: utf-8 -*-
"""Install/uninstall hooks.

The Home Console becomes the default landing by setting
``res.users.action_id`` to the Home Console client action for every user
that has no custom landing yet. On uninstall we clear those pointers so
users fall back to Odoo's default behaviour.
"""

_ACTION_XMLID = "custom_home_console.action_home_console"


def _post_init_set_default_home(env):
    action = env.ref(_ACTION_XMLID, raise_if_not_found=False)
    if not action:
        return
    # Only touch users with no custom landing yet (do not override
    # explicit per-user choices made by admins).
    users = env["res.users"].sudo().search([("action_id", "=", False)])
    users.write({"action_id": action.id})


def _uninstall_clear_default_home(env):
    action = env.ref(_ACTION_XMLID, raise_if_not_found=False)
    if not action:
        return
    env["res.users"].sudo().search([("action_id", "=", action.id)]).write({"action_id": False})
