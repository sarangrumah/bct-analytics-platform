# -*- coding: utf-8 -*-
"""Post-init hook: make every internal user a 'Petty Cash / User'.

Done here rather than in XML because writing ``implied_ids`` on
``base.group_user`` through the ORM is what retroactively materialises the
group onto all existing internal users (and onto every user created
afterwards); the equivalent data record does not propagate reliably.
"""

import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    base_user = env.ref("base.group_user", raise_if_not_found=False)
    petty_user = env.ref("custom_petty_cash.group_petty_cash_user", raise_if_not_found=False)
    if not base_user or not petty_user:
        return
    if petty_user not in base_user.implied_ids:
        base_user.write({"implied_ids": [(4, petty_user.id)]})
        _logger.info("custom_petty_cash: granted 'Petty Cash / User' to all internal users.")
