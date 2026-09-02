# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class PpobVaTopup(models.Model):
    _name = "custom.ppob.va.topup"
    _description = "Virtual Account Topup"
    _order = "create_date desc, id desc"
    _inherit = ["mail.thread"]

    name = fields.Char(
        required=True,
        copy=False,
        default=lambda self: self.env["ir.sequence"].next_by_code("custom.ppob.va.topup") or "/",
    )
    va_account_id = fields.Many2one("custom.ppob.va.account", required=True, ondelete="restrict")
    mitra_id = fields.Many2one(related="va_account_id.mitra_id", store=True)
    wallet_id = fields.Many2one(related="va_account_id.wallet_id", store=True)
    amount = fields.Monetary(required=True, currency_field="currency_id")
    bank_ref = fields.Char(required=True, copy=False, index=True, help="Bank-side reference. Used as idempotency key.")
    source = fields.Selection(
        selection=[
            ("csv_import", "CSV Import"),
            ("h2h_callback", "H2H Callback"),
            ("manual_post", "Manual Post"),
        ],
        required=True,
    )
    state = fields.Selection(
        selection=[
            ("received", "Received"),
            ("settled", "Settled"),
            ("credited", "Credited"),
            ("rejected", "Rejected"),
        ],
        default="received",
        required=True,
        tracking=True,
    )
    statement_line_id = fields.Many2one("account.bank.statement.line", ondelete="set null")
    wallet_move_id = fields.Many2one("custom.ppob.wallet.move", ondelete="set null", copy=False)
    note = fields.Char()
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )

    _bank_ref_uniq = models.Constraint(
        "unique(bank_ref)",
        "Bank reference must be unique (idempotency key).",
    )

    def action_credit_wallet(self):
        """Post the credit leg to the mitra wallet. Idempotent."""
        for topup in self:
            if topup.wallet_move_id:
                continue
            if topup.state not in ("received", "settled"):
                raise UserError(_("Topup %s in state %s cannot be credited.") % (topup.name, topup.state))
            counterpart = topup.va_account_id.transit_account_id
            if not counterpart:
                raise UserError(
                    _("VA account %s has no transit account configured.") % topup.va_account_id.display_name
                )
            output_tax = topup.va_account_id.output_tax_id
            reason = f"VA Topup {topup.name} ({topup.bank_ref})"
            if output_tax:
                wallet_move = topup.wallet_id._atomic_credit_with_tax(
                    gross_amount=topup.amount,
                    output_tax=output_tax,
                    reason=reason,
                    counterpart_account=counterpart,
                    move_type="topup",
                    va_topup_id=topup.id,
                )
            else:
                wallet_move = topup.wallet_id._atomic_credit(
                    amount=topup.amount,
                    reason=reason,
                    counterpart_account=counterpart,
                    move_type="topup",
                    va_topup_id=topup.id,
                )
            topup.write(
                {
                    "wallet_move_id": wallet_move.id,
                    "state": "credited",
                }
            )
        return True

    def action_reject(self):
        for t in self:
            if t.state == "credited":
                raise UserError(_("Cannot reject an already-credited topup."))
            t.state = "rejected"
