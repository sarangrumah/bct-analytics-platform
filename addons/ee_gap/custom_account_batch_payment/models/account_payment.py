# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountPayment(models.Model):
    _inherit = "account.payment"

    batch_payment_id = fields.Many2one(
        "custom.account.batch.payment",
        string="Batch Payment",
        copy=False,
        index="btree_not_null",
    )

    @api.model
    def action_create_batch_from_selection(self):
        """List-view action: wrap the selected posted payments into a new batch."""
        payments = self.browse(self.env.context.get("active_ids", []))
        payments = payments.filtered(lambda p: p.state in ("in_process", "paid"))
        if not payments:
            raise UserError(_("Select posted payments first."))
        if len(payments.journal_id) > 1:
            raise UserError(_("All payments must share one journal."))
        if len(set(payments.mapped("payment_type"))) > 1:
            raise UserError(_("Mix of inbound and outbound payments — pick one direction."))
        already = payments.filtered("batch_payment_id")
        if already:
            raise UserError(
                _("Already in a batch: %s") % ", ".join("%s (%s)" % (p.name, p.batch_payment_id.name) for p in already)
            )
        batch = self.env["custom.account.batch.payment"].create(
            {
                "journal_id": payments.journal_id.id,
                "batch_type": payments[0].payment_type,
                "payment_ids": [(6, 0, payments.ids)],
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "custom.account.batch.payment",
            "res_id": batch.id,
            "view_mode": "form",
        }
