# -*- coding: utf-8 -*-
import base64

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountBatchPayment(models.Model):
    """A batch of posted payments on one bank journal, exported as one
    transfer file (EE ``account_batch_payment`` stand-in)."""

    _name = "custom.account.batch.payment"
    _description = "Batch Payment"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"

    name = fields.Char(readonly=True, copy=False, default=lambda self: self.env._("New"))
    journal_id = fields.Many2one(
        "account.journal",
        required=True,
        domain="[('type', 'in', ('bank', 'cash'))]",
        check_company=True,
        tracking=True,
    )
    company_id = fields.Many2one("res.company", related="journal_id.company_id", store=True)
    currency_id = fields.Many2one("res.currency", related="journal_id.currency_id")
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    batch_type = fields.Selection(
        [("outbound", "Outbound (vendor payments)"), ("inbound", "Inbound (customer payments)")],
        required=True,
        default="outbound",
        tracking=True,
    )
    payment_ids = fields.One2many("account.payment", "batch_payment_id", string="Payments")
    payment_count = fields.Integer(compute="_compute_totals")
    amount_total = fields.Monetary(compute="_compute_totals", currency_field="currency_id", store=False)
    export_format_id = fields.Many2one("custom.batch.payment.format", string="Export Format")
    export_file = fields.Binary(readonly=True, copy=False, attachment=True)
    export_filename = fields.Char(readonly=True, copy=False)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("validated", "Validated"),
            ("sent", "Sent"),
            ("reconciled", "Reconciled"),
        ],
        default="draft",
        tracking=True,
        compute="_compute_state",
        store=True,
        readonly=False,
    )

    @api.depends("payment_ids", "payment_ids.amount")
    def _compute_totals(self):
        for batch in self:
            batch.payment_count = len(batch.payment_ids)
            batch.amount_total = sum(batch.payment_ids.mapped("amount"))

    @api.depends("payment_ids.is_matched", "payment_ids.state")
    def _compute_state(self):
        # Only the sent → reconciled hop is automatic: every payment paid and
        # matched with a bank statement. The other transitions stay manual.
        for batch in self:
            if (
                batch.state == "sent"
                and batch.payment_ids
                and all(p.state == "paid" and p.is_matched for p in batch.payment_ids)
            ):
                batch.state = "reconciled"
            else:
                batch.state = batch.state or "draft"

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_validate(self):
        for batch in self:
            if not batch.payment_ids:
                raise UserError(_("Batch %s has no payments.") % batch.name)
            bad_state = batch.payment_ids.filtered(lambda p: p.state not in ("in_process", "paid"))
            if bad_state:
                raise UserError(_("These payments are not posted: %s") % ", ".join(bad_state.mapped("name")))
            bad_journal = batch.payment_ids.filtered(lambda p: p.journal_id != batch.journal_id)
            if bad_journal:
                raise UserError(
                    _("These payments use another journal than the batch's: %s") % ", ".join(bad_journal.mapped("name"))
                )
            bad_dir = batch.payment_ids.filtered(lambda p: p.payment_type != batch.batch_type)
            if bad_dir:
                raise UserError(
                    _("These payments do not match the batch direction (%s): %s")
                    % (batch.batch_type, ", ".join(bad_dir.mapped("name")))
                )
            if batch.name in (False, _("New")):
                batch.name = self.env["ir.sequence"].next_by_code("custom.account.batch.payment") or _("New")
            batch.state = "validated"
        return True

    def action_generate_export_file(self):
        self.ensure_one()
        if self.state not in ("validated", "sent"):
            raise UserError(_("Validate the batch before exporting."))
        fmt = self.export_format_id
        if not fmt:
            raise UserError(_("Pick an export format first."))
        content, filename = fmt.render(self)
        self.write(
            {
                "export_file": base64.b64encode(content),
                "export_filename": filename,
                "state": "sent",
            }
        )
        self.payment_ids.filtered(lambda p: not p.is_sent).mark_as_sent()
        return True

    def action_draft(self):
        for batch in self:
            batch.payment_ids.filtered("is_sent").unmark_as_sent()
            batch.state = "draft"
        return True

    def action_open_payments(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Payments"),
            "res_model": "account.payment",
            "view_mode": "list,form",
            "domain": [("batch_payment_id", "=", self.id)],
        }

    def unlink(self):
        if any(b.state not in ("draft",) for b in self):
            raise UserError(_("Only draft batches can be deleted. Reset to draft first."))
        return super().unlink()
