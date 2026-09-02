# -*- coding: utf-8 -*-
from __future__ import annotations

from odoo import _, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    bast_count = fields.Integer(
        string="BAST",
        compute="_compute_bast_count",
    )

    def _bast_reference(self):
        """The value stored in custom.bast.document.reference for this order."""
        self.ensure_one()
        return "%s,%d" % (self._name, self.id)

    def _bast_domain(self):
        self.ensure_one()
        return [("reference", "=", self._bast_reference())]

    def _compute_bast_count(self):
        Bast = self.env["custom.bast.document"]
        for order in self:
            order.bast_count = Bast.search_count(order._bast_domain()) if order.id else 0

    def _ensure_bast_module(self):
        if "custom.bast.document" not in self.env:
            raise UserError(_("Module 'custom_bast' is not installed."))

    def _bast_lines_vals(self):
        """One BAST line per real order line (sections/notes skipped)."""
        self.ensure_one()
        vals = []
        for line in self.order_line:
            if line.display_type:
                continue
            vals.append(
                (
                    0,
                    0,
                    {
                        "item_description": line.name or line.product_id.display_name or "-",
                        "product_id": line.product_id.id or False,
                        "qty": line.product_uom_qty,
                        "uom_id": line.product_uom_id.id or False,
                    },
                )
            )
        return vals

    def action_generate_bast(self):
        self.ensure_one()
        self._ensure_bast_module()
        if not self.partner_id:
            raise UserError(_("Set a customer before generating a BAST."))
        doc = (
            self.env["custom.bast.document"]
            .sudo()
            .create(
                {
                    "kind": "delivery",
                    # delivery = company hands the goods over to the customer
                    "party_from_id": self.company_id.partner_id.id,
                    "party_to_id": self.partner_id.id,
                    "company_id": self.company_id.id,
                    "reference": self._bast_reference(),
                    "line_ids": self._bast_lines_vals(),
                }
            )
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("BAST"),
            "res_model": "custom.bast.document",
            "res_id": doc.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_bast(self):
        self.ensure_one()
        self._ensure_bast_module()
        return {
            "type": "ir.actions.act_window",
            "name": _("BAST Documents"),
            "res_model": "custom.bast.document",
            "view_mode": "list,form",
            "domain": self._bast_domain(),
            "context": {
                "default_reference": self._bast_reference(),
                "default_kind": "delivery",
                "default_party_from_id": self.company_id.partner_id.id,
                "default_party_to_id": self.partner_id.id,
            },
        }
