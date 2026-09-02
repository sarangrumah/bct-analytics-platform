# -*- coding: utf-8 -*-
"""Invoice generation for rental.order.

Lines built:
  1. Rental fee — ``daily_rate × (days_actual or days_planned)``
  2. Late penalty (compute-based 50% surcharge) — if > 0
  3. Cumulative late fee accrual from cron — if > 0
  4. Damage lines — one per BAST return line with ``condition != 'good'``
     (price 0 by default; user edits before posting)
  5. (Optional, if custom_rental_bom_explosion installed) memo lines
     listing exploded BOM components (qty 0, price 0) for transparency
"""

from __future__ import annotations

from odoo import _, fields, models
from odoo.exceptions import UserError


class RentalOrder(models.Model):
    _inherit = "rental.order"

    invoice_id = fields.Many2one(
        "account.move",
        string="Invoice",
        copy=False,
        readonly=True,
    )
    invoice_state = fields.Selection(
        related="invoice_id.state",
        string="Invoice State",
        readonly=True,
    )

    def _auto_invoice_enabled(self):
        return self.env["ir.config_parameter"].sudo().get_param(
            "custom_rental_invoicing.auto_invoice_on_return", "False"
        ) in ("True", "true", "1", True)

    def _resolve_income_account(self):
        """Return the income account to post the rental fee against.

        Order: asset.product.property_account_income_id →
        product.categ_id.property_account_income_categ_id → first
        income-type account in company.
        """
        self.ensure_one()
        product = self.asset_id.product_id
        if product:
            acc = product.property_account_income_id or product.categ_id.property_account_income_categ_id
            if acc:
                return acc
        return (
            self.env["account.account"]
            .sudo()
            .search(
                [
                    ("account_type", "=", "income"),
                    ("company_ids", "in", self.company_id.id),
                ],
                limit=1,
            )
        )

    def _build_invoice_line_vals(self):
        """Return list of (0, 0, vals) tuples for invoice lines."""
        self.ensure_one()
        product = self.asset_id.product_id
        income_acc = self._resolve_income_account()
        if not income_acc:
            raise UserError(
                _("No income account configured for product/company; cannot invoice rental %s.") % self.name
            )

        lines = []
        days = self.days_actual or self.days_planned or 1.0

        # 1. Rental fee
        rental_subtotal = self.daily_rate * days
        lines.append(
            (
                0,
                0,
                {
                    "name": _("Rental fee: %(asset)s — %(days).1f day(s) × %(rate)s")
                    % {
                        "asset": self.asset_id.display_name,
                        "days": days,
                        "rate": self.daily_rate,
                    },
                    "quantity": days,
                    "price_unit": self.daily_rate,
                    "account_id": income_acc.id,
                    "product_id": product.id if product else False,
                    "product_uom_id": product.uom_id.id if product else False,
                    "tax_ids": [(6, 0, [])],
                },
            )
        )

        # 2. Late penalty (compute-based)
        if self.late_penalty and self.late_penalty > 0:
            lines.append(
                (
                    0,
                    0,
                    {
                        "name": _("Late penalty (50%% surcharge on overdue days)"),
                        "quantity": 1.0,
                        "price_unit": self.late_penalty,
                        "account_id": income_acc.id,
                        "tax_ids": [(6, 0, [])],
                    },
                )
            )

        # 3. Cumulative cron-accrued late fee
        if self.late_fee_total and self.late_fee_total > 0:
            lines.append(
                (
                    0,
                    0,
                    {
                        "name": _("Late fee (accrued %d day(s) post-due)") % len(self.late_fee_line_ids),
                        "quantity": 1.0,
                        "price_unit": self.late_fee_total,
                        "account_id": income_acc.id,
                        "tax_ids": [(6, 0, [])],
                    },
                )
            )

        # 4. Damage lines from BAST return
        if self.bast_return_id and self.bast_return_id.line_ids:
            for bl in self.bast_return_id.line_ids:
                if bl.condition == "good":
                    continue
                lines.append(
                    (
                        0,
                        0,
                        {
                            "name": _("Damage: %(item)s [%(cond)s]")
                            % {
                                "item": bl.item_description,
                                "cond": dict(bl._fields["condition"].selection).get(bl.condition),
                            },
                            "quantity": bl.qty or 1.0,
                            "price_unit": 0.0,
                            "account_id": income_acc.id,
                            "product_id": bl.product_id.id if bl.product_id else False,
                            "tax_ids": [(6, 0, [])],
                        },
                    )
                )

        # 5. Memo section listing exploded BOM components (if available).
        # Bundle is charged at headline level (line 1); these are note-type lines
        # for transparency. Detail is also in BAST attachment.
        if hasattr(self.asset_id, "_explode_components"):
            components = self.asset_id._explode_components(qty=1.0)
            if components:
                lines.append(
                    (
                        0,
                        0,
                        {
                            "display_type": "line_section",
                            "name": _("Bundle components (per BOM):"),
                        },
                    )
                )
                for comp in components:
                    if not comp.get("product"):
                        continue
                    lines.append(
                        (
                            0,
                            0,
                            {
                                "display_type": "line_note",
                                "name": _("• %(qty)s × %(name)s")
                                % {
                                    "qty": comp["qty"],
                                    "name": comp["product"].display_name,
                                },
                            },
                        )
                    )

        return lines

    def action_create_invoice(self):
        Move = self.env["account.move"].sudo()
        created = self.env["account.move"]
        for rec in self:
            if rec.invoice_id:
                raise UserError(_("Rental %s already has invoice %s.") % (rec.name, rec.invoice_id.name))
            if rec.state not in ("returned", "picked_up"):
                raise UserError(_("Only picked-up or returned rentals can be invoiced."))
            journal = (
                self.env["account.journal"]
                .sudo()
                .search([("company_id", "=", rec.company_id.id), ("type", "=", "sale")], limit=1)
            )
            if not journal:
                raise UserError(_("No sales journal in company '%s'.") % rec.company_id.name)
            line_vals = rec._build_invoice_line_vals()
            move = Move.with_company(rec.company_id).create(
                {
                    "move_type": "out_invoice",
                    "partner_id": rec.partner_id.id,
                    "company_id": rec.company_id.id,
                    "currency_id": rec.currency_id.id,
                    "journal_id": journal.id,
                    "invoice_date": fields.Date.context_today(rec),
                    "ref": rec.name,
                    "invoice_origin": rec.name,
                    "invoice_line_ids": line_vals,
                }
            )
            rec.invoice_id = move.id
            rec.message_post(
                body=_("Invoice <a href='#' data-oe-model='account.move' data-oe-id='%(id)s'>%(name)s</a> created.")
                % {"id": move.id, "name": move.name or "draft"},
            )
            rec._pdp_audit_write("rental_invoice_created", rec.id, {"invoice_id": move.id})
            created |= move
        if len(created) == 1:
            return {
                "type": "ir.actions.act_window",
                "res_model": "account.move",
                "res_id": created.id,
                "view_mode": "form",
                "target": "current",
            }
        return True

    def action_return(self):
        res = super().action_return()
        if self._auto_invoice_enabled():
            for rec in self:
                if rec.state == "returned" and not rec.invoice_id:
                    try:
                        rec.action_create_invoice()
                    except UserError as e:
                        rec.message_post(body=_("Auto-invoice skipped: %s") % e)
        return res
