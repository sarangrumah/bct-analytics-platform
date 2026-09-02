"""Backfill base.group_user.implied_ids on existing installs.

The base.group_user override in security/security.xml lives in <data noupdate="1">
following Odoo's convention (cf. addons/purchase/security/purchase_security.xml).
That means it only applies on fresh install. For databases where this module was
installed BEFORE 19.0.0.1.1, the implication is missing — this migration adds it.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return  # fresh install: XML data handles it

    from odoo import api, SUPERUSER_ID
    from odoo.api import Environment

    env = Environment(cr, SUPERUSER_ID, {})

    group_user = env.ref("base.group_user", raise_if_not_found=False)
    ai_bridge_user = env.ref("custom_ai_bridge.group_custom_ai_user", raise_if_not_found=False)
    ai_features_user = env.ref("custom_ai_features.group_ai_user", raise_if_not_found=False)

    if not (group_user and ai_bridge_user and ai_features_user):
        _logger.warning("custom_ai_features 0.1.1 migration: one or more groups missing, skipping")
        return

    to_link = [g for g in (ai_bridge_user, ai_features_user) if g not in group_user.implied_ids]
    if not to_link:
        _logger.info("custom_ai_features 0.1.1 migration: implications already present, nothing to do")
        return

    group_user.write({"implied_ids": [(4, g.id) for g in to_link]})
    _logger.info(
        "custom_ai_features 0.1.1 migration: added %s to base.group_user.implied_ids",
        [g.name for g in to_link],
    )
