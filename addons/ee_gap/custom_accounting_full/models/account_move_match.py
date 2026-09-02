# -*- coding: utf-8 -*-
"""Hook 3-way match into vendor bill posting."""

from __future__ import annotations

import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AccountMoveMatch(models.Model):
    _inherit = "account.move"

    custom_match_result_id = fields.Many2one(
        "custom.match.result",
        readonly=True,
        copy=False,
    )
    custom_match_status = fields.Selection(
        related="custom_match_result_id.overall_status",
        store=True,
        readonly=True,
    )

    def _custom_get_match_policy(self):
        self.ensure_one()
        Policy = self.env["custom.match.policy"].sudo()
        return Policy.search(
            [
                ("company_id", "=", self.company_id.id),
                ("active", "=", True),
            ],
            limit=1,
        )

    def _custom_compute_match(self):
        """Build a fresh custom.match.result for this vendor bill."""
        self.ensure_one()
        if self.move_type != "in_invoice":
            return False
        Result = self.env["custom.match.result"].sudo()
        LineResult = self.env["custom.match.line.result"].sudo()
        policy = self._custom_get_match_policy()
        qty_tol = policy.qty_tolerance_percent if policy else 0.0
        price_tol = policy.price_tolerance_percent if policy else 0.0
        if self.custom_match_result_id:
            self.custom_match_result_id.unlink()
        line_vals = []
        any_po = False
        statuses = []
        for ml in self.invoice_line_ids.filtered(lambda l: l.display_type == "product"):
            po_line = ml.purchase_line_id
            if not po_line:
                line_vals.append(
                    {
                        "bill_line_id": ml.id,
                        "billed_qty": ml.quantity,
                        "unit_price_bill": ml.price_unit,
                        "status": "pass",  # no PO — handled at overall level
                    }
                )
                continue
            any_po = True
            ordered_qty = po_line.product_qty
            received_qty = po_line.qty_received
            billed_qty = ml.quantity
            qty_var_pct = ((billed_qty - received_qty) / received_qty * 100.0) if received_qty else 0.0
            price_var_pct = (
                ((ml.price_unit - po_line.price_unit) / po_line.price_unit * 100.0) if po_line.price_unit else 0.0
            )
            line_status = "pass"
            qty_off = abs(qty_var_pct) > qty_tol
            price_off = abs(price_var_pct) > price_tol
            if qty_off and price_off:
                line_status = "both"
            elif qty_off:
                line_status = "qty_variance"
            elif price_off:
                line_status = "price_variance"
            statuses.append(line_status)
            line_vals.append(
                {
                    "bill_line_id": ml.id,
                    "po_line_id": po_line.id,
                    "ordered_qty": ordered_qty,
                    "received_qty": received_qty,
                    "billed_qty": billed_qty,
                    "qty_variance_pct": qty_var_pct,
                    "unit_price_po": po_line.price_unit,
                    "unit_price_bill": ml.price_unit,
                    "price_variance_pct": price_var_pct,
                    "status": line_status,
                }
            )
        overall = "pass"
        if not any_po:
            overall = "no_po"
        elif "both" in statuses or ("qty_variance" in statuses and "price_variance" in statuses):
            overall = "both"
        elif "qty_variance" in statuses:
            overall = "qty_variance"
        elif "price_variance" in statuses:
            overall = "price_variance"
        result = Result.create(
            {
                "move_id": self.id,
                "overall_status": overall,
            }
        )
        for v in line_vals:
            v["result_id"] = result.id
            LineResult.create(v)
        self.custom_match_result_id = result.id
        return result

    def _post(self, soft=True):
        # Run 3-way match BEFORE posting so we can block with UserError.
        self._custom_run_three_way_match()
        return super()._post(soft=soft)

    def _custom_run_three_way_match(self):
        for move in self.filtered(lambda m: m.move_type == "in_invoice"):
            try:
                move._custom_compute_match()
            except Exception as exc:  # noqa: BLE001
                _logger.exception("3-way match compute failed: %s", exc)
                continue
            policy = move._custom_get_match_policy()
            if not policy:
                continue
            status = move.custom_match_status
            if status in ("pass", "no_po"):
                continue
            if status == "qty_variance":
                action = policy.on_qty_mismatch
            elif status == "price_variance":
                action = policy.on_price_mismatch
            elif status == "both":
                # Take the stricter of the two
                action = (
                    "block"
                    if "block"
                    in (
                        policy.on_qty_mismatch,
                        policy.on_price_mismatch,
                    )
                    else "warn"
                )
            else:
                action = "warn"
            if action == "block":
                raise UserError(
                    _(
                        "Vendor bill %(name)s blocked by 3-way match "
                        "(status=%(s)s). Resolve the variance or relax the policy.",
                        name=move.display_name,
                        s=status,
                    )
                )
            move.message_post(
                body=_(
                    "3-way match warning: %(s)s.",
                    s=status,
                )
            )
        return True
