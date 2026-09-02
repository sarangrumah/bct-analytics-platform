# -*- coding: utf-8 -*-
"""POS order: IDR rounding for cash change, e-receipt dispatch, loyalty accrual."""

import logging
import math

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


# Map x_rupiah_rounding -> integer step in IDR.
_ROUNDING_STEPS = {
    "none": 0,
    "50": 50,
    "100": 100,
    "500": 500,
    "1000": 1000,
}

# 1 loyalty point per IDR 10,000.
_LOYALTY_IDR_PER_POINT = 10000


def _round_amount(amount: float, step: int, strategy: str) -> float:
    """Round ``amount`` to a multiple of ``step`` using ``strategy``.

    ``strategy`` is one of ``up`` / ``down`` / ``nearest``. ``step <= 0``
    returns the amount unchanged.
    """
    if step <= 0:
        return amount
    quotient = amount / step
    if strategy == "up":
        return math.ceil(quotient) * step
    if strategy == "down":
        return math.floor(quotient) * step
    # nearest
    return round(quotient) * step


class PosOrder(models.Model):
    _inherit = "pos.order"

    # ---------- e-receipt routing ----------

    x_eperformance_receipt_sent = fields.Boolean(
        string="E-Receipt Sent",
        default=False,
        copy=False,
        tracking=True,
    )
    x_eperformance_receipt_channel = fields.Selection(
        [
            ("whatsapp", "WhatsApp"),
            ("sms", "SMS"),
            ("email", "Email"),
            ("print", "Printed"),
            ("none", "None"),
        ],
        string="E-Receipt Channel",
        default="none",
        copy=False,
        tracking=True,
    )

    # ---------- IDR rounding ----------

    x_idr_rounding_applied = fields.Monetary(
        string="IDR Rounding Adjustment",
        currency_field="currency_id",
        default=0.0,
        copy=False,
        readonly=True,
        help="Amount added or subtracted from the cash kembalian to round to "
        "the nearest IDR step configured on the POS.",
    )
    x_idr_rounded_change = fields.Monetary(
        string="Rounded Change",
        currency_field="currency_id",
        compute="_compute_idr_rounded_change",
        store=False,
        help="The cash change after applying the IDR rounding step.",
    )

    # ---------- loyalty ----------

    x_loyalty_points_earned = fields.Integer(
        string="Loyalty Points Earned",
        compute="_compute_loyalty_points",
        store=True,
        help="Loyalty points earned on this order (1 point per IDR 10,000).",
    )
    x_loyalty_credited = fields.Boolean(
        string="Loyalty Credited",
        default=False,
        copy=False,
        readonly=True,
    )

    # ---------- compute ----------

    @api.depends("amount_total")
    def _compute_loyalty_points(self):
        for order in self:
            total = order.amount_total or 0.0
            order.x_loyalty_points_earned = int(math.floor(total / _LOYALTY_IDR_PER_POINT))

    @api.depends("amount_paid", "amount_total", "config_id", "payment_ids")
    def _compute_idr_rounded_change(self):
        for order in self:
            order.x_idr_rounded_change = order._idr_round_change_amount()

    # ---------- IDR rounding logic ----------

    def _is_cash_payment(self):
        """Return True if any payment line on the order is cash."""
        self.ensure_one()
        for pay in self.payment_ids:
            method = pay.payment_method_id
            # Standard pos.payment.method has ``is_cash_count`` (cash drawer
            # impact). Fall back to journal type when not present.
            if getattr(method, "is_cash_count", False):
                return True
            journal = getattr(method, "journal_id", False)
            if journal and getattr(journal, "type", False) == "cash":
                return True
        return False

    def _idr_round_change_amount(self):
        """Compute the cash change after IDR rounding.

        Falls back to the raw ``amount_paid - amount_total`` when the POS
        config has no rounding or no cash payment is present.
        """
        self.ensure_one()
        raw_change = (self.amount_paid or 0.0) - (self.amount_total or 0.0)
        config = self.config_id
        if not config or not self._is_cash_payment():
            return raw_change
        step = _ROUNDING_STEPS.get(config.x_rupiah_rounding or "none", 0)
        if step <= 0:
            return raw_change
        strategy = config.x_rupiah_rounding_strategy or "nearest"
        return _round_amount(raw_change, step, strategy)

    def action_apply_idr_rounding(self):
        """Persist the rounding adjustment on the order.

        Computes ``x_idr_rounding_applied`` = rounded_change - raw_change.
        Idempotent: calling twice on the same order is a no-op.
        """
        for order in self:
            if not order._is_cash_payment():
                continue
            raw_change = (order.amount_paid or 0.0) - (order.amount_total or 0.0)
            rounded = order._idr_round_change_amount()
            order.x_idr_rounding_applied = rounded - raw_change
        return True

    # ---------- loyalty wallet credit ----------

    def action_credit_loyalty(self):
        """Add ``x_loyalty_points_earned`` to the customer's wallet balance.

        Skips orders that already credited (``x_loyalty_credited=True``) or
        have no partner attached.
        """
        for order in self:
            if order.x_loyalty_credited:
                continue
            partner = order.partner_id
            if not partner:
                _logger.info(
                    "Skip loyalty credit on order %s: no partner attached",
                    order.name,
                )
                continue
            points = order.x_loyalty_points_earned or 0
            if points <= 0:
                order.x_loyalty_credited = True
                continue
            partner.sudo().write(
                {
                    "x_loyalty_balance": (partner.x_loyalty_balance or 0) + points,
                }
            )
            order.x_loyalty_credited = True
            _logger.info(
                "Credited %s loyalty points to partner %s from order %s",
                points,
                partner.display_name,
                order.name,
            )
        return True

    # ---------- e-receipt dispatch (real) ----------

    def _build_ereceipt_body(self):
        """Render the e-receipt body. Plain text suitable for WA/SMS."""
        self.ensure_one()
        lines = []
        lines.append(_("Order: %s") % (self.name or ""))
        if self.partner_id:
            lines.append(_("Customer: %s") % self.partner_id.display_name)
        lines.append("")
        lines.append(_("Items:"))
        for line in self.lines:
            qty = getattr(line, "qty", 0)
            product = getattr(line, "product_id", False)
            subtotal = getattr(line, "price_subtotal_incl", 0.0)
            lines.append(f"- {product.display_name if product else ''} x{qty} = {subtotal:,.0f}")
        lines.append("")
        lines.append(_("Total: IDR %s") % f"{self.amount_total:,.0f}")
        if self.x_loyalty_points_earned:
            lines.append(_("Loyalty points: +%s") % self.x_loyalty_points_earned)
        return "\n".join(lines)

    def _partner_phone(self):
        self.ensure_one()
        if not self.partner_id:
            return False
        return self.partner_id.mobile or self.partner_id.phone or False

    def _send_ereceipt_whatsapp(self):
        self.ensure_one()
        config = self.config_id
        account = config.x_whatsapp_account_id if config else False
        if not account:
            raise UserError(_("No WhatsApp account configured on POS %s.") % (config.name if config else ""))
        phone = self._partner_phone()
        if not phone:
            raise UserError(_("Customer has no phone number for WhatsApp delivery."))
        msg = (
            self.env["whatsapp.message"]
            .sudo()
            .create(
                {
                    "account_id": account.id,
                    "to_phone": phone,
                    "to_partner_id": self.partner_id.id,
                    "body": self._build_ereceipt_body(),
                    "direction": "outbound",
                    "state": "draft",
                }
            )
        )
        try:
            msg.action_send()
        except Exception as e:
            _logger.warning(
                "POS WA e-receipt failed order=%s: %s",
                self.name,
                e,
            )
            raise
        return msg

    def _send_ereceipt_sms(self):
        self.ensure_one()
        config = self.config_id
        account = config.x_sms_account_id if config else False
        if not account:
            raise UserError(_("No SMS account configured on POS %s.") % (config.name if config else ""))
        phone = self._partner_phone()
        if not phone:
            raise UserError(_("Customer has no phone number for SMS delivery."))
        msg = (
            self.env["custom.sms.message"]
            .sudo()
            .create(
                {
                    "account_id": account.id,
                    "to_phone": phone,
                    "to_partner_id": self.partner_id.id,
                    "body": self._build_ereceipt_body(),
                    "purpose": "transactional",
                    "state": "draft",
                }
            )
        )
        try:
            msg.action_send()
        except Exception as e:
            _logger.warning(
                "POS SMS e-receipt failed order=%s: %s",
                self.name,
                e,
            )
            raise
        return msg

    def action_send_ereceipt(self):
        """Dispatch the POS e-receipt through the configured channel."""
        for order in self:
            channel = order.x_eperformance_receipt_channel or "none"
            if channel == "whatsapp":
                order._send_ereceipt_whatsapp()
            elif channel == "sms":
                order._send_ereceipt_sms()
            elif channel == "email":
                _logger.info(
                    "POS e-receipt (Email) dispatch requested for order %s",
                    order.name,
                )
            else:
                _logger.info(
                    "POS e-receipt skipped for order %s (channel=%s)",
                    order.name,
                    channel,
                )
            order.write({"x_eperformance_receipt_sent": True})
        return True
