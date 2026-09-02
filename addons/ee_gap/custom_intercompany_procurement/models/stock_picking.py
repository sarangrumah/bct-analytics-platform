# -*- coding: utf-8 -*-
"""Mirror outgoing stock picking validation as an incoming picking in the
receiving sister company.

Triggered on ``_action_done`` (i.e. after the picking is validated and
moves are processed). Idempotent via ``x_custom_ic_mirror_picking_id``.
"""

from __future__ import annotations

import logging

from odoo import _, fields, models

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _name = "stock.picking"
    _inherit = ["stock.picking", "pdp.audited.mixin"]

    x_custom_ic_mirror_picking_id = fields.Many2one(
        "stock.picking",
        string="Intercompany Mirror Picking",
        readonly=True,
        copy=False,
    )
    x_custom_ic_source_picking_id = fields.Many2one(
        "stock.picking",
        string="Intercompany Source Picking",
        readonly=True,
        copy=False,
    )
    x_custom_ic_rule_id = fields.Many2one(
        "account.intercompany.rule",
        string="Intercompany Rule",
        readonly=True,
        copy=False,
    )

    def _pdp_audit_classification(self):
        return "internal"

    def _action_done(self):
        res = super()._action_done()
        for picking in self:
            picking._custom_run_ic_picking_mirror()
        return res

    def _custom_run_ic_picking_mirror(self):
        self.ensure_one()
        # Skip if already mirrored or this IS a mirror
        if self.x_custom_ic_mirror_picking_id or self.x_custom_ic_source_picking_id:
            return
        # Only mirror outgoing pickings
        if self.picking_type_code != "outgoing":
            return
        if not self.partner_id:
            return
        rule = self._custom_find_ic_picking_rule()
        if not rule:
            return
        try:
            mirror = self._custom_create_ic_mirror_picking(rule)
        except Exception as e:  # pragma: no cover
            _logger.exception("IC picking mirror failed for %s: %s", self.id, e)
            self.message_post(body=_("Intercompany picking mirror FAILED: %s") % e)
            return
        if mirror:
            self.write(
                {
                    "x_custom_ic_mirror_picking_id": mirror.id,
                    "x_custom_ic_rule_id": rule.id,
                }
            )
            self._pdp_audit_write(
                "ic_picking_mirror_created",
                self.id,
                {"rule": rule.name, "mirror_picking": mirror.id, "mirror_company": rule.company_to_id.name},
            )

    def _custom_find_ic_picking_rule(self):
        self.ensure_one()
        partner = self.partner_id.commercial_partner_id
        receiver = self.env["res.company"].sudo().search([("partner_id", "=", partner.id)], limit=1)
        if not receiver or receiver == self.company_id:
            return self.env["account.intercompany.rule"]
        return (
            self.env["account.intercompany.rule"]
            .sudo()
            .search(
                [
                    ("active", "=", True),
                    ("mirror_picking", "=", True),
                    ("company_from_id", "=", self.company_id.id),
                    ("company_to_id", "=", receiver.id),
                ],
                limit=1,
            )
        )

    def _custom_create_ic_mirror_picking(self, rule):
        self.ensure_one()
        target_company = rule.company_to_id
        warehouse = rule.target_warehouse_id or self.env["stock.warehouse"].sudo().search(
            [("company_id", "=", target_company.id)], limit=1
        )
        if not warehouse:
            raise ValueError(_("No warehouse in receiving company '%s'.") % target_company.name)
        ptype = (
            self.env["stock.picking.type"]
            .sudo()
            .search(
                [
                    ("code", "=", "incoming"),
                    ("warehouse_id", "=", warehouse.id),
                ],
                limit=1,
            )
        )
        if not ptype:
            raise ValueError(_("No incoming picking type for warehouse '%s'.") % warehouse.name)
        loc_src = ptype.default_location_src_id or self.env.ref("stock.stock_location_suppliers")
        loc_dst = ptype.default_location_dest_id or warehouse.lot_stock_id
        source_partner = self.company_id.partner_id

        move_vals = []
        for move in self.move_ids:
            if move.state == "cancel":
                continue
            move_vals.append(
                (
                    0,
                    0,
                    {
                        "name": move.product_id.display_name,
                        "product_id": move.product_id.id,
                        "product_uom_qty": move.product_uom_qty,
                        "product_uom": move.product_uom.id,
                        "location_id": loc_src.id,
                        "location_dest_id": loc_dst.id,
                        "company_id": target_company.id,
                    },
                )
            )
        if not move_vals:
            return self.env["stock.picking"]

        return (
            self.env["stock.picking"]
            .with_company(target_company)
            .sudo()
            .create(
                {
                    "picking_type_id": ptype.id,
                    "partner_id": source_partner.id,
                    "location_id": loc_src.id,
                    "location_dest_id": loc_dst.id,
                    "origin": _("IC mirror of %s/%s") % (self.company_id.name, self.name or self.id),
                    "company_id": target_company.id,
                    "x_custom_ic_source_picking_id": self.id,
                    "x_custom_ic_rule_id": rule.id,
                    "move_ids": move_vals,
                }
            )
        )
