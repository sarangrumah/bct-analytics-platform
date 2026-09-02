# -*- coding: utf-8 -*-
"""Bring existing databases in line with the 19.0.0.10.0 data files.

``data/retail_import_profiles.xml`` is ``noupdate="1"``, so widening the X48 column map
only affects fresh installs. Existing tenants get it here.

Without this, ``_post_x48`` never sees the source NET SOLD AMOUNT / TAX AMOUNT columns
and silently falls back to deriving the tax as ``total/(1+rate)`` — which is exactly the
per-line rounding drift this release removes.
"""

import json
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

_MODULE = "custom_retail_import"

# X48 Customer Return Report, 1-based columns:
#   21 CATEGORY, 28 NET DISCOUNT AMOUNT, 29 NET SOLD AMOUNT, 30 TAX AMOUNT
# (X48 has no TAX RATE column — a line is taxed iff it carries a tax amount.)
_X48_NEW_COLUMNS = {
    "category": 21,
    "net_discount": 28,
    "net_amount": 29,
    "tax_amount": 30,
}


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    profile = env.ref("%s.profile_retail_x48" % _MODULE, raise_if_not_found=False)
    if not profile:
        return

    try:
        columns = json.loads(profile.column_map_json or "{}")
    except ValueError:
        _logger.warning("custom_retail_import: profile_retail_x48 has an unparseable column_map_json; leaving it alone")
        return

    added = {k: v for k, v in _X48_NEW_COLUMNS.items() if k not in columns}
    if not added:
        return

    columns.update(added)
    profile.column_map_json = json.dumps(columns)
    _logger.info("custom_retail_import: profile_retail_x48 column map += %s", sorted(added))
