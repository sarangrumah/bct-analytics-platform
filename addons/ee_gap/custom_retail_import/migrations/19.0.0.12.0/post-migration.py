# -*- coding: utf-8 -*-
"""Bring existing databases in line with the 19.0.0.12.0 data files.

``data/retail_import_profiles.xml`` is ``noupdate="1"``, so widening the X24DN column
map only affects fresh installs. Existing tenants get it here.

Without this, ``_post_x24`` never sees the cashier, the four discount slots or the
member/notes columns, and the posted pos.order carries none of them.
"""

import json
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

_MODULE = "custom_retail_import"

# X24DN Retail Sales Detail Report, 1-based columns.
_X24_NEW_COLUMNS = {
    "staff_id": 10,
    "staff_name": 11,
    "item_type": 12,
    "line_id": 13,
    "brand": 18,
    "class": 20,
    "subclass": 21,
    "discount_type_1": 31,
    "discount_code_1": 32,
    "discount_description_1": 33,
    "discount_amount_1": 34,
    "discount_percentage_1": 35,
    "discount_type_2": 36,
    "discount_code_2": 37,
    "discount_description_2": 38,
    "discount_amount_2": 39,
    "discount_percentage_2": 40,
    "discount_type_3": 41,
    "discount_code_3": 42,
    "discount_description_3": 43,
    "discount_amount_3": 44,
    "discount_percentage_3": 45,
    "discount_type_4": 46,
    "discount_code_4": 47,
    "discount_description_4": 48,
    "discount_amount_4": 49,
    "discount_percentage_4": 50,
    "line_comment": 51,
    "transaction_note": 52,
    "member_id": 56,
    "member_type": 57,
    "customer_phone": 58,
    "omni_order_id": 59,
}


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    profile = env.ref("%s.profile_retail_x24" % _MODULE, raise_if_not_found=False)
    if not profile:
        return

    try:
        columns = json.loads(profile.column_map_json or "{}")
    except ValueError:
        _logger.warning("custom_retail_import: profile_retail_x24 has an unparseable column_map_json; leaving it alone")
        return

    added = {k: v for k, v in _X24_NEW_COLUMNS.items() if k not in columns}
    if not added:
        return

    columns.update(added)
    profile.column_map_json = json.dumps(columns)
    _logger.info("custom_retail_import: profile_retail_x24 column map += %s", sorted(added))
