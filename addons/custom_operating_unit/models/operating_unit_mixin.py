# Part of custom_operating_unit. Licence: LGPL-3.
"""The stamped ``operating_unit_id`` field, and the four stock models that carry it.

Every occurrence is declared through ``operating_unit_field()`` so the four models cannot drift
apart in ``store``, ``index`` or ``ondelete``. The warehouse groups by this column, so those
options are load-bearing, not decoration:

* ``store=True``  - a non-stored compute has no Postgres column, so logical decoding never sees it
                    and ``dim_operating_unit`` could not be built at all.
* ``index=True``  - creates ``<table>_operating_unit_id_index``; the marts group by it on every
                    query and the Phase 4 budget is p95 under 2 s with 12 months of data.
* ``ondelete="restrict"`` - deleting a unit that facts still reference would orphan history.

``custom_ppob`` imports ``operating_unit_field`` and declares the same column on
``ppob.transaction``, which is why this helper is module-public.
"""

from odoo import fields, models


def operating_unit_field(string="Operating Unit", required=False):
    """Return a uniformly configured ``operating_unit_id`` many2one."""
    return fields.Many2one(
        "operating.unit",
        string=string,
        store=True,
        index=True,
        required=required,
        ondelete="restrict",
        default=lambda self: self.env.user.default_operating_unit_id.id or False,
        domain="[('company_id', '=', company_id)]",
        help="Unit of operation this document belongs to. The analytics warehouse groups by it.",
    )


class SaleOrder(models.Model):
    _inherit = "sale.order"

    operating_unit_id = operating_unit_field()


class AccountMove(models.Model):
    _inherit = "account.move"

    operating_unit_id = operating_unit_field()


class StockPicking(models.Model):
    _inherit = "stock.picking"

    operating_unit_id = operating_unit_field()


class PosOrder(models.Model):
    _inherit = "pos.order"

    operating_unit_id = operating_unit_field()
