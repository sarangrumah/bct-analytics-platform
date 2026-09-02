# -*- coding: utf-8 -*-
"""Extend account.intercompany.rule with procurement-side toggles.

The base rule (custom_accounting_full) already handles account.move
mirroring. Here we add:

* ``mirror_purchase_order`` — PO confirmed in A → draft SO created in B
* ``mirror_picking``        — outgoing picking validated in A → incoming
                              picking created in B

Both default OFF for safety. Receiving-side journals/warehouses must
exist; if not, mirror fails gracefully and posts a chatter note.
"""

from __future__ import annotations

from odoo import fields, models


class IntercompanyRule(models.Model):
    _inherit = "account.intercompany.rule"

    mirror_purchase_order = fields.Boolean(
        string="Mirror Purchase Orders",
        default=False,
        help="When a PO is confirmed in the issuing company against the "
        "partner that represents the receiving company, auto-create a "
        "draft sales order in the receiving company.",
    )
    mirror_picking = fields.Boolean(
        string="Mirror Stock Pickings",
        default=False,
        help="When an outgoing picking is validated in the issuing company "
        "against the receiving company's partner, auto-create a matching "
        "incoming picking in the receiving company.",
    )
    target_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Receiving Warehouse",
        domain="[('company_id', '=', company_to_id)]",
        help="Default warehouse where mirrored incoming pickings land. "
        "If empty, the first warehouse of the receiving company is used.",
    )
    target_sale_journal_id = fields.Many2one(
        "account.journal",
        string="Receiving SO Source",
        domain="[('company_id', '=', company_to_id), ('type', '=', 'sale')]",
        help="(Reserved) Future: journal used when receiving company invoices the mirrored SO.",
    )

    # --- Asset-loan (drone rental) spawn config ---------------------------
    # When the mirrored SO in the receiving company is confirmed and carries
    # the loan service line, auto-create an internal asset-loan rental.order.
    spawn_rental_loan = fields.Boolean(
        string="Spawn Asset-Loan on SO Confirm",
        default=False,
        help="When the mirrored sales order is confirmed and contains the Loan "
        "Service line, auto-create a draft internal asset-loan (rental.order) in "
        "the receiving company. The physical unit stays as an internal transfer; "
        "only the service line is invoiced.",
    )
    loan_service_product_id = fields.Many2one(
        "product.product",
        string="Loan Service Product",
        domain="[('type', '=', 'service')]",
        help="Service product (e.g. 'Jasa Sewa Drone') whose presence on the SO "
        "marks it as an asset loan and whose qty becomes the primary loan qty.",
    )
    loan_asset_product_id = fields.Many2one(
        "product.product",
        string="Loan Asset Product",
        help="Physical, serial-tracked asset moved on loan (e.g. the drone).",
    )
    loan_on_loan_location_id = fields.Many2one(
        "stock.location",
        string="On-Loan Location",
        domain="[('usage', '=', 'internal')]",
        help="Internal location the asset sits in while on loan (Internal->Internal).",
    )
