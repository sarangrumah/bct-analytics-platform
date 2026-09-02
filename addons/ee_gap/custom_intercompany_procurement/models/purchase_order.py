# -*- coding: utf-8 -*-
"""Mirror PO confirmation as a draft SO in the receiving sister company."""

from __future__ import annotations

import logging

from odoo import _, fields, models

_logger = logging.getLogger(__name__)


class PurchaseOrder(models.Model):
    _name = "purchase.order"
    _inherit = ["purchase.order", "pdp.audited.mixin"]

    x_custom_ic_mirror_so_id = fields.Many2one(
        "sale.order",
        string="Intercompany Mirror SO",
        readonly=True,
        copy=False,
        help="Sales order auto-generated in the sister company.",
    )
    x_custom_ic_source_so_id = fields.Many2one(
        "sale.order",
        string="Intercompany Source SO",
        readonly=True,
        copy=False,
        help="If this PO was created BY a mirror flow, points back to the source SO.",
    )
    x_custom_ic_rule_id = fields.Many2one(
        "account.intercompany.rule",
        string="Intercompany Rule",
        readonly=True,
        copy=False,
    )

    def _pdp_audit_classification(self):
        return "financial"

    def button_confirm(self):
        res = super().button_confirm()
        for po in self:
            po._custom_run_ic_po_mirror()
        return res

    def _custom_run_ic_po_mirror(self):
        self.ensure_one()
        if self.x_custom_ic_mirror_so_id or self.x_custom_ic_source_so_id:
            return
        rule = self._custom_find_ic_po_rule()
        if not rule:
            return
        try:
            so = self._custom_create_ic_mirror_so(rule)
        except Exception as e:  # pragma: no cover
            _logger.exception("IC PO mirror failed for %s: %s", self.id, e)
            self.message_post(body=_("Intercompany PO mirror FAILED: %s") % e)
            return
        if so:
            self.write(
                {
                    "x_custom_ic_mirror_so_id": so.id,
                    "x_custom_ic_rule_id": rule.id,
                }
            )
            self._pdp_audit_write(
                "ic_po_mirror_created",
                self.id,
                {"rule": rule.name, "mirror_so": so.id, "mirror_company": rule.company_to_id.name},
            )

    def _custom_find_ic_po_rule(self):
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
                    ("mirror_purchase_order", "=", True),
                    ("company_from_id", "=", self.company_id.id),
                    ("company_to_id", "=", receiver.id),
                ],
                limit=1,
            )
        )

    def _custom_create_ic_mirror_so(self, rule):
        self.ensure_one()
        target_company = rule.company_to_id
        target_partner = self.company_id.partner_id
        warehouse = rule.target_warehouse_id or self.env["stock.warehouse"].sudo().search(
            [("company_id", "=", target_company.id)], limit=1
        )
        if not warehouse:
            raise ValueError(_("No warehouse in receiving company '%s'.") % target_company.name)

        order_lines = []
        skipped_physical = self.env["product.product"]
        for pol in self.order_line:
            # For asset-loan rules, only the service line may cross intercompany.
            # A physical (non-service) product must never become an SO delivery
            # line — that would post COGS and derecognise the fixed asset. The
            # asset moves separately via an Internal->Internal loan transfer.
            if rule.spawn_rental_loan and pol.product_id.type != "service":
                skipped_physical |= pol.product_id
                continue
            order_lines.append(
                (
                    0,
                    0,
                    {
                        "product_id": pol.product_id.id,
                        "name": pol.name,
                        "product_uom_qty": pol.product_qty,
                        "product_uom_id": pol.product_uom_id.id,
                        "price_unit": pol.price_unit,
                        "tax_ids": [(6, 0, [])],
                    },
                )
            )
        if skipped_physical:
            self.message_post(
                body=_(
                    "Intercompany asset-loan mirror: skipped physical line(s) %s — "
                    "only the service line crosses intercompany; the asset moves via "
                    "an internal loan transfer."
                )
                % ", ".join(skipped_physical.mapped("display_name"))
            )
        if not order_lines:
            raise ValueError(
                _("No service line to mirror on PO %s. An asset-loan PO must carry the loan service product.")
                % (self.name or self.id)
            )

        return (
            self.env["sale.order"]
            .with_company(target_company)
            .sudo()
            .create(
                {
                    "partner_id": target_partner.id,
                    "company_id": target_company.id,
                    "warehouse_id": warehouse.id,
                    "origin": _("IC mirror of %s/%s") % (self.company_id.name, self.name or self.id),
                    "client_order_ref": self.name,
                    "x_custom_ic_source_po_id": self.id,
                    "x_custom_ic_rule_id": rule.id,
                    "order_line": order_lines,
                }
            )
        )


class SaleOrder(models.Model):
    _inherit = "sale.order"

    x_custom_ic_source_po_id = fields.Many2one(
        "purchase.order",
        string="Intercompany Source PO",
        readonly=True,
        copy=False,
    )
    x_custom_ic_rule_id = fields.Many2one(
        "account.intercompany.rule",
        string="Intercompany Rule",
        readonly=True,
        copy=False,
    )
