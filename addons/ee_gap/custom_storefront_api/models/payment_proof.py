# -*- coding: utf-8 -*-
"""Manual bank-transfer proof submitted from the headless storefront.

Manual transfer is the native ``payment_custom`` wire-transfer provider
(code ``custom``): the order's ``payment.transaction`` sits in ``pending``
until an admin confirms receipt. To give that confirmation a paper trail,
the storefront lets the customer upload a transfer proof (amount, bank
reference, date, image). Each submission is one record here; a payments
manager reviews it and, on :meth:`action_verify`, settles the linked
transaction (``_set_done``).
"""

from __future__ import annotations

from odoo import _, api, fields, models


class StorefrontPaymentProof(models.Model):
    _name = "custom.storefront.payment.proof"  # nosemgrep
    _description = "Storefront Manual Transfer Proof"
    _order = "create_date desc, id desc"
    _rec_name = "name"

    name = fields.Char(string="Reference", default="New", copy=False, readonly=True)
    transaction_id = fields.Many2one(
        "payment.transaction", string="Payment Transaction", ondelete="set null", index=True
    )
    sale_order_id = fields.Many2one("sale.order", string="Order", index=True)
    partner_id = fields.Many2one("res.partner", string="Customer")
    provider_id = fields.Many2one(
        "payment.provider", string="Provider", related="transaction_id.provider_id", store=True
    )
    currency_id = fields.Many2one("res.currency", string="Currency")
    amount = fields.Monetary(string="Amount Paid", currency_field="currency_id")
    bank_reference = fields.Char(string="Bank / Transfer Reference")
    sender_name = fields.Char(string="Sender Name")
    paid_date = fields.Date(string="Transfer Date")
    proof_image = fields.Image(string="Proof Image", max_width=1920, max_height=1920)
    proof_filename = fields.Char(string="Proof Filename")
    note = fields.Text(string="Customer Note")
    state = fields.Selection(
        [("submitted", "Submitted"), ("verified", "Verified"), ("rejected", "Rejected")],
        default="submitted",
        required=True,
        index=True,
    )
    transaction_state = fields.Selection(related="transaction_id.state", string="Transaction State", readonly=True)
    reviewed_by = fields.Many2one("res.users", string="Reviewed By", readonly=True)
    reviewed_on = fields.Datetime(string="Reviewed On", readonly=True)
    review_note = fields.Text(string="Review Note")

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if not rec.name or rec.name == "New":
                rec.name = f"PRF/{rec.id:05d}"
        return records

    def action_verify(self):
        """Confirm the transfer: settle the transaction and stamp the review."""
        for rec in self:
            tx = rec.transaction_id
            if tx and tx.state not in ("done", "cancel"):
                tx._set_done()
            rec.write(
                {
                    "state": "verified",
                    "reviewed_by": self.env.user.id,
                    "reviewed_on": fields.Datetime.now(),
                }
            )
            if rec.sale_order_id:
                rec.sale_order_id.message_post(
                    body=_("Manual transfer proof %(ref)s verified by %(user)s.")
                    % {"ref": rec.name, "user": self.env.user.name}
                )
        return True

    def action_reject(self):
        """Reject the proof; the transaction stays pending for a re-submission."""
        for rec in self:
            rec.write(
                {
                    "state": "rejected",
                    "reviewed_by": self.env.user.id,
                    "reviewed_on": fields.Datetime.now(),
                }
            )
            if rec.sale_order_id:
                rec.sale_order_id.message_post(
                    body=_("Manual transfer proof %(ref)s rejected by %(user)s.")
                    % {"ref": rec.name, "user": self.env.user.name}
                )
        return True
