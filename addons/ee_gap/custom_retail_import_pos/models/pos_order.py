# -*- coding: utf-8 -*-
"""Transaction-level columns of the source retail workbook, kept on the pos.order.

X24DN repeats the cashier, the member and the transaction notes on every line of a
transaction. They carry no GL effect, but without them a posted pos.order cannot be
traced back to the row that produced it, and finance has to reopen the workbook to
answer "who sold this" or "which member bought it".

Populated by ``retail.import.executor._post_x24`` / ``_post_x48`` through
``_ri_src_order_vals``, which silently drops any field this module does not define —
so the importer keeps working on a tenant without POS installed.
"""

from odoo import fields, models


class PosOrder(models.Model):
    _inherit = "pos.order"

    ri_staff_id = fields.Char(
        string="Source Staff ID",
        readonly=True,
        help="STAFF ID as it appears in the source retail workbook (X24DN).",
    )
    ri_staff_name = fields.Char(
        string="Source Staff Name",
        readonly=True,
        help="STAFF NAME as it appears in the source retail workbook (X24DN).",
    )
    ri_member_id = fields.Char(
        string="Source Member ID",
        readonly=True,
        help="ALTERNATE ID / MEMBER ID as it appears in the source retail workbook.",
    )
    ri_member_type = fields.Char(
        string="Source Member Type",
        readonly=True,
        help="MEMBER TYPE as it appears in the source retail workbook (e.g. STAMPS_ID).",
    )
    ri_customer_phone = fields.Char(
        string="Source Customer Phone",
        readonly=True,
        help="TELEPHONE NUMBER as it appears in the source retail workbook.",
    )
    ri_transaction_note = fields.Char(
        string="Source Transaction Note",
        readonly=True,
        help="TRANSACTION NOTES as it appears in the source retail workbook.",
    )
    ri_omni_order_id = fields.Char(
        string="Source Omni Order ID",
        readonly=True,
        index="btree_not_null",
        help="Omni Order Id as it appears in the source retail workbook — the join key "
        "back to the e-commerce order for an omnichannel sale.",
    )
