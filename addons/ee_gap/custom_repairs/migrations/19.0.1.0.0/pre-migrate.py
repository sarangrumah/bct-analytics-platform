# -*- coding: utf-8 -*-
"""Clean up obsolete customer-facing repair columns and the warranty matrix.

custom_repairs is reoriented from external-customer product repair to
internal asset maintenance. The warranty and customer-notification fields
are removed and the warranty-matrix model is dropped. Odoo does not drop
DB columns/tables automatically on field/model removal, so do it here.
Repurposed fields (x_id_complaint, x_returned, x_return_date,
x_return_reason) keep their columns and data — only labels changed.
"""

import logging

_logger = logging.getLogger(__name__)

OBSOLETE_COLUMNS = (
    "x_warranty_status",
    "x_warranty_until",
    "x_serial_number",
    "x_purchase_date",
    "x_customer_notified",
)


def migrate(cr, version):
    if not version:
        return
    for column in OBSOLETE_COLUMNS:
        cr.execute("ALTER TABLE repair_order DROP COLUMN IF EXISTS %s" % column)
    cr.execute("DROP TABLE IF EXISTS custom_repairs_warranty_matrix CASCADE")
    _logger.info(
        "custom_repairs: dropped obsolete columns %s and warranty matrix table",
        ", ".join(OBSOLETE_COLUMNS),
    )
