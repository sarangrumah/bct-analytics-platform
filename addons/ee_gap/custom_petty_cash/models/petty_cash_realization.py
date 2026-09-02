# -*- coding: utf-8 -*-
"""Petty cash realization — pertanggungjawaban penggunaan uang muka.

On posting, each realization line is turned into GL:

* ``third_party`` lines are grouped per vendor into a **vendor bill**
  (``account.move`` / ``in_invoice``). PPN rides on ``tax_ids``; PPh
  withholding + Coretax bukti potong are applied automatically by
  ``custom_tax_id`` on posting. The bill is then paid out of the advance
  through the dedicated Petty Cash payment journal (Dr AP / Cr Uang Muka).
* ``expense`` lines are accumulated into a single journal entry
  (Dr Expense / Cr Uang Muka Petty Cash).
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PettyCashRealization(models.Model):
    _name = "petty.cash.realization"
    _description = "Petty Cash Realization"
    _inherit = ["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]
    _order = "date desc, id desc"

    name = fields.Char(
        string="Reference",
        required=True,
        default=lambda self: self.env._("New"),
        copy=False,
        tracking=True,
    )
    request_id = fields.Many2one(
        "petty.cash.request",
        string="Petty Cash Request",
        required=True,
        ondelete="restrict",
        tracking=True,
        domain="[('state', 'in', ('disbursed', 'in_realization'))]",
    )
    employee_id = fields.Many2one(related="request_id.employee_id", store=True, string="Employee")
    company_id = fields.Many2one(related="request_id.company_id", store=True, string="Company")
    currency_id = fields.Many2one(related="request_id.currency_id", store=True)
    l10n_ou_analytic_id = fields.Many2one(related="request_id.l10n_ou_analytic_id", store=True, string="Operating Unit")
    date = fields.Date(string="Realization Date", default=fields.Date.context_today, required=True, tracking=True)
    line_ids = fields.One2many("petty.cash.realization.line", "realization_id", string="Lines")

    amount_total = fields.Monetary(currency_field="currency_id", compute="_compute_amount_total", store=True)
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("submitted", "Submitted"),
            ("posted", "Posted"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
        copy=False,
    )
    bill_ids = fields.One2many("account.move", "petty_cash_realization_id", string="Journal Entries")
    bill_count = fields.Integer(compute="_compute_bill_count")

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends("line_ids.price_total")
    def _compute_amount_total(self):
        for rec in self:
            rec.amount_total = sum(rec.line_ids.mapped("price_total"))

    def _compute_bill_count(self):
        for rec in self:
            rec.bill_count = len(rec.bill_ids)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("petty.cash.realization") or _("New")
        return super().create(vals_list)

    @api.constrains("line_ids")
    def _check_third_party_attachment(self):
        for rec in self:
            for line in rec.line_ids.filtered(lambda l: l.line_type == "third_party"):
                if not line.attachment_ids:
                    raise UserError(
                        _("Third-party line '%s' requires the supplier invoice to be attached.") % (line.name or "?")
                    )
                if not line.partner_id:
                    raise UserError(_("Third-party line '%s' requires a vendor.") % (line.name or "?"))

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_submit(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only draft realizations can be submitted."))
            if not rec.line_ids:
                raise UserError(_("Add at least one line before submitting."))
            rec.state = "submitted"
            rec.message_post(body=_("Submitted for Finance posting."), subtype_xmlid="mail.mt_note")
        return True

    def action_post(self):
        self.ensure_one()
        if self.state not in ("draft", "submitted"):
            raise UserError(_("Only draft/submitted realizations can be posted."))
        if not self.line_ids:
            raise UserError(_("Nothing to post."))
        self._check_third_party_attachment()

        third_party = self.line_ids.filtered(lambda l: l.line_type == "third_party")
        expense = self.line_ids.filtered(lambda l: l.line_type == "expense")

        for partner in third_party.mapped("partner_id"):
            self._post_vendor_bill(partner, third_party.filtered(lambda l: l.partner_id == partner))
        if expense:
            self._post_expense_entry(expense)

        self.state = "posted"
        self.request_id._on_realization_posted()
        self.message_post(body=_("Realization posted."), subtype_xmlid="mail.mt_note")
        return True

    def action_cancel(self):
        for rec in self:
            if rec.bill_ids.filtered(lambda m: m.state == "posted"):
                raise UserError(_("Reverse the posted entries before cancelling this realization."))
            rec.state = "cancelled"
        return True

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state == "posted":
                raise UserError(_("Cannot reset a posted realization."))
            rec.state = "draft"
        return True

    # ------------------------------------------------------------------
    # Accounting — third-party vendor bill
    # ------------------------------------------------------------------
    def _post_vendor_bill(self, partner, lines):
        self.ensure_one()
        request = self.request_id
        invoice_lines = []
        for line in lines:
            vals = {
                "name": line.name or self.name,
                "product_id": line.product_id.id or False,
                "account_id": line.account_id.id or False,
                "quantity": 1.0,
                "price_unit": line.price_subtotal,
                "tax_ids": [fields.Command.set(line.tax_ids.ids)],
            }
            if (
                line.x_custom_withholding_category_id
                and "x_custom_withholding_category_id" in self.env["account.move.line"]._fields
            ):
                vals["x_custom_withholding_category_id"] = line.x_custom_withholding_category_id.id
            invoice_lines.append(fields.Command.create(request._pc_line_analytic(vals)))

        move_vals = {
            "move_type": "in_invoice",
            "partner_id": partner.id,
            "invoice_date": self.date,
            "date": self.date,
            "ref": self.name,
            "petty_cash_request_id": request.id,
            "petty_cash_realization_id": self.id,
            "invoice_line_ids": invoice_lines,
        }
        move = self.env["account.move"].with_company(self.company_id)
        if "l10n_purchase_type" in move._fields:
            move_vals["l10n_purchase_type"] = "non_trade"
        # OU + Employee are stamped directly into each line's
        # analytic_distribution (see request._pc_line_analytic); we deliberately
        # do NOT set the header l10n_ou_analytic_id, so the localization's
        # OU-recompute never fires and cannot clobber the employee tag.
        bill = move.create(move_vals)
        bill.action_post()
        self._pay_bill_from_advance(bill)
        return bill

    def _pay_bill_from_advance(self, bill):
        """Register a payment on ``bill`` through the Petty Cash payment
        journal so the counterpart hits the advance account
        (Dr AP / Cr Uang Muka Petty Cash), then reconciles the bill."""
        self.ensure_one()
        journal = self.company_id.petty_cash_payment_journal_id
        if not journal:
            raise UserError(
                _("Set a Petty Cash Payment journal in Accounting Settings before posting third-party lines.")
            )
        advance = self.request_id._pc_advance_account()
        self._configure_payment_journal(journal, advance)

        register = (
            self.env["account.payment.register"]
            .with_company(self.company_id)
            .with_context(active_model="account.move", active_ids=bill.ids)
            .create(
                {
                    "journal_id": journal.id,
                    "payment_date": self.date,
                }
            )
        )
        payments = register._create_payments()
        for pay in payments:
            if pay.move_id:
                pay.move_id.write(
                    {
                        "petty_cash_request_id": self.request_id.id,
                        "petty_cash_realization_id": self.id,
                    }
                )
        return payments

    @staticmethod
    def _configure_payment_journal(journal, account):
        """Point every payment-method line of ``journal`` at ``account`` so the
        payment's liquidity counterpart is the advance account, not a bank
        outstanding account (mirrors the localization's ``_set_all_outstanding``)."""
        lines = journal.inbound_payment_method_line_ids | journal.outbound_payment_method_line_ids
        for line in lines:
            if line.payment_account_id != account:
                line.payment_account_id = account.id

    # ------------------------------------------------------------------
    # Accounting — plain expense entry
    # ------------------------------------------------------------------
    def _post_expense_entry(self, lines):
        self.ensure_one()
        request = self.request_id
        advance = request._pc_advance_account()
        partner = request._pc_employee_partner()
        journal = self.company_id.petty_cash_expense_journal_id or self.env["account.journal"].search(
            [("type", "=", "general"), ("company_id", "=", self.company_id.id)], limit=1
        )
        if not journal:
            raise UserError(
                _("No general journal found for the expense entry. Configure a Petty Cash Expense journal.")
            )

        move_lines = []
        total = 0.0
        for line in lines:
            if not line.account_id:
                raise UserError(_("Expense line '%s' needs an account.") % (line.name or "?"))
            amount = line.price_total  # tax-inclusive for non-creditable petty expenses
            total += amount
            move_lines.append(
                fields.Command.create(
                    request._pc_line_analytic(
                        {
                            "name": line.name or self.name,
                            "account_id": line.account_id.id,
                            "partner_id": partner.id,
                            "debit": amount,
                            "credit": 0.0,
                        }
                    )
                )
            )
        move_lines.append(
            fields.Command.create(
                {
                    "name": _("Petty Cash realization — %s") % self.name,
                    "account_id": advance.id,
                    "partner_id": partner.id,
                    "debit": 0.0,
                    "credit": total,
                }
            )
        )
        move = (
            self.env["account.move"]
            .with_company(self.company_id)
            .create(
                {
                    "move_type": "entry",
                    "journal_id": journal.id,
                    "date": self.date,
                    "ref": self.name,
                    "petty_cash_request_id": request.id,
                    "petty_cash_realization_id": self.id,
                    "line_ids": move_lines,
                }
            )
        )
        move.action_post()
        return move

    # ------------------------------------------------------------------
    # Smart button
    # ------------------------------------------------------------------
    def action_view_bills(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Journal Entries"),
            "res_model": "account.move",
            "domain": [("petty_cash_realization_id", "=", self.id)],
            "view_mode": "list,form",
        }
