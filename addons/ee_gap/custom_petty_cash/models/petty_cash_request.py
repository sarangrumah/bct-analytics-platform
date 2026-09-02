# -*- coding: utf-8 -*-
"""Petty cash request — pengajuan + pencairan (Bank Out) + settlement.

Lifecycle::

    draft ─submit─▶ to_approve ─approve─▶ approved ─disburse─▶ disbursed
        ─(realizations posted)─▶ in_realization ─settle─▶ settled

Approval routes through the generic ``custom_approval_engine`` matrix; when
no matrix matches the request is approved directly by a Finance user.

Accounting (per employee, via the Petty Cash Advance account)::

    Disburse    Dr Uang Muka Petty Cash / Cr Bank
    Realize 3rd Dr Expense+PPN / Cr AP   then   Dr AP / Cr Uang Muka
    Realize exp Dr Expense / Cr Uang Muka
    Return      Dr Bank / Cr Uang Muka
    Reimburse   Dr Uang Muka / Cr Bank
    Settle      advance lines net to zero and are reconciled
"""

from __future__ import annotations

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PettyCashRequest(models.Model):
    _name = "petty.cash.request"
    _description = "Petty Cash Request"
    _inherit = ["mail.thread", "mail.activity.mixin", "approval.mixin", "pdp.audited.mixin"]
    _order = "request_date desc, id desc"

    name = fields.Char(
        string="Reference",
        required=True,
        default=lambda self: self.env._("New"),
        copy=False,
        tracking=True,
    )
    employee_id = fields.Many2one(
        "hr.employee",
        string="Employee",
        required=True,
        tracking=True,
        default=lambda self: self.env.user.employee_id,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )
    l10n_ou_analytic_id = fields.Many2one(
        "account.analytic.account",
        string="Operating Unit",
        domain="[('plan_id.name', '=', 'Operating Unit')]",
        tracking=True,
        help="Operating Unit the petty cash is requested for. Stamped onto "
        "every generated journal item when the localization is installed.",
    )
    request_date = fields.Date(
        string="Request Date",
        default=fields.Date.context_today,
        required=True,
        tracking=True,
    )
    purpose = fields.Char(string="Purpose", tracking=True)
    line_ids = fields.One2many(
        "petty.cash.request.line",
        "request_id",
        string="Estimate Breakdown",
    )
    amount_requested = fields.Monetary(
        string="Amount Requested",
        currency_field="currency_id",
        tracking=True,
        help="Amount the employee is asking for. When an estimate breakdown is entered it must match this total.",
    )
    realization_deadline = fields.Date(
        string="Realization Deadline",
        tracking=True,
        help="Date by which the employee must submit the realization.",
    )
    bank_journal_id = fields.Many2one(
        "account.journal",
        string="Bank-Out Journal",
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', company_id)]",
    )
    advance_account_id = fields.Many2one(
        "account.account",
        string="Advance Account",
        domain="[('reconcile', '=', True)]",
    )

    disburse_move_id = fields.Many2one("account.move", string="Disbursement Entry", copy=False, readonly=True)
    move_ids = fields.One2many("account.move", "petty_cash_request_id", string="Journal Entries")
    move_count = fields.Integer(compute="_compute_move_count")
    realization_ids = fields.One2many("petty.cash.realization", "request_id", string="Realizations")
    realization_count = fields.Integer(compute="_compute_realization_count")

    amount_disbursed = fields.Monetary(currency_field="currency_id", compute="_compute_amounts", store=True)
    amount_realized = fields.Monetary(currency_field="currency_id", compute="_compute_amounts", store=True)
    amount_outstanding = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_amounts",
        store=True,
        help="Net balance of the advance account still tied to this request "
        "(positive = employee holds cash to return; negative = company owes "
        "the employee).",
    )

    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("to_approve", "To Approve"),
            ("approved", "Approved"),
            ("disbursed", "Disbursed"),
            ("in_realization", "In Realization"),
            ("settled", "Settled"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
        copy=False,
    )
    is_overdue = fields.Boolean(compute="_compute_is_overdue", search="_search_is_overdue")

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    def _compute_move_count(self):
        for rec in self:
            rec.move_count = len(rec.move_ids)

    def _compute_realization_count(self):
        for rec in self:
            rec.realization_count = len(rec.realization_ids)

    @api.depends(
        "move_ids.state",
        "move_ids.line_ids.debit",
        "move_ids.line_ids.credit",
        "advance_account_id",
        "realization_ids.state",
        "realization_ids.amount_total",
    )
    def _compute_amounts(self):
        for rec in self:
            adv_lines = rec._advance_move_lines(posted_only=True)
            debit = sum(adv_lines.mapped("debit"))
            credit = sum(adv_lines.mapped("credit"))
            rec.amount_disbursed = debit
            rec.amount_outstanding = debit - credit
            rec.amount_realized = sum(
                rec.realization_ids.filtered(lambda r: r.state == "posted").mapped("amount_total")
            )

    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.is_overdue = bool(
                rec.state in ("disbursed", "in_realization")
                and rec.realization_deadline
                and rec.realization_deadline < today
                and not rec.currency_id.is_zero(rec.amount_outstanding)
            )

    def _search_is_overdue(self, operator, value):
        today = fields.Date.context_today(self)
        overdue_domain = [
            ("state", "in", ("disbursed", "in_realization")),
            ("realization_deadline", "<", today),
        ]
        # `is_overdue = True` → overdue domain; `= False` → its inverse.
        if (operator == "=" and value) or (operator == "!=" and not value):
            return overdue_domain
        return ["!", "&"] + overdue_domain[:1] + overdue_domain[1:]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _advance_move_lines(self, posted_only=True):
        """Journal items on the advance account tied to this request."""
        self.ensure_one()
        advance = self._pc_advance_account(soft=True)
        if not advance:
            return self.env["account.move.line"]
        lines = self.move_ids.line_ids.filtered(lambda l: l.account_id == advance)
        if posted_only:
            lines = lines.filtered(lambda l: l.parent_state == "posted")
        return lines

    def _pc_advance_account(self, soft=False):
        self.ensure_one()
        account = self.advance_account_id or self.company_id.petty_cash_advance_account_id
        if not account and not soft:
            raise UserError(_("Set a Petty Cash Advance account on the request or in Accounting Settings first."))
        return account

    def _pc_bank_journal(self):
        self.ensure_one()
        journal = self.bank_journal_id or self.company_id.petty_cash_bank_out_journal_id
        if not journal:
            raise UserError(_("Set a Petty Cash Bank-Out journal on the request or in Accounting Settings first."))
        return journal

    def _pc_employee_partner(self):
        self.ensure_one()
        emp = self.employee_id
        partner = emp.work_contact_id or (emp.user_id and emp.user_id.partner_id)
        if not partner:
            raise UserError(
                _("Employee %s has no linked contact (Work Contact / User) to book the advance against.") % emp.name
            )
        return partner

    def _pc_analytic_distribution(self, base=None):
        """Merge the Operating Unit and the Employee analytic accounts into
        ``base`` as two *separate plan tags* on the same 100% slice.

        analytic_distribution keys are comma-joined analytic-account ids (one
        per plan); OU (plan "Operating Unit") and Employee (plan "Employee")
        each contribute one id, so a line carries e.g. ``{"<ou>,<emp>": 100}``.
        Both dimensions stay independently groupable in analytic reporting.
        Built directly (not via the ``l10n_ou_analytic_id`` line field) so no
        recompute can clobber the employee tag.
        """
        self.ensure_one()
        ids = []
        if self.l10n_ou_analytic_id:
            ids.append(self.l10n_ou_analytic_id.id)
        if self.employee_id:
            ids.append(self.employee_id._pc_get_analytic_account().id)
        if not ids:
            return dict(base or {})
        add = ",".join(str(i) for i in ids)
        dist = dict(base or {})
        if not dist:
            return {add: 100.0}
        # Append our plan ids to every existing key (idempotent per id).
        merged = {}
        for key, pct in dist.items():
            key_ids = key.split(",")
            for i in add.split(","):
                if i not in key_ids:
                    key_ids.append(i)
            merged[",".join(key_ids)] = pct
        return merged

    def _pc_line_analytic(self, vals=None):
        """Return move-line ``vals`` with the combined OU+Employee analytic
        distribution stamped in."""
        self.ensure_one()
        vals = dict(vals or {})
        dist = self._pc_analytic_distribution(vals.get("analytic_distribution"))
        if dist:
            vals["analytic_distribution"] = dist
        return vals

    # ------------------------------------------------------------------
    # CRUD / onchange
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("petty.cash.request") or _("New")
        return super().create(vals_list)

    @api.onchange("company_id")
    def _onchange_company_defaults(self):
        for rec in self:
            rec.advance_account_id = rec.company_id.petty_cash_advance_account_id
            rec.bank_journal_id = rec.company_id.petty_cash_bank_out_journal_id

    @api.constrains("line_ids", "amount_requested")
    def _check_breakdown_total(self):
        for rec in self:
            if rec.line_ids:
                total = sum(rec.line_ids.mapped("amount"))
                if not rec.currency_id.is_zero(total - rec.amount_requested):
                    raise UserError(
                        _(
                            "The estimate breakdown (%(bd)s) does not match the requested amount (%(req)s).",
                            bd=total,
                            req=rec.amount_requested,
                        )
                    )

    # ------------------------------------------------------------------
    # Workflow — approval
    # ------------------------------------------------------------------
    def action_submit(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only draft requests can be submitted."))
            if rec.amount_requested <= 0:
                raise UserError(_("Enter the amount requested before submitting."))
            rec.state = "to_approve"
            # Auto-create + submit an approval request when a matrix matches;
            # otherwise the request just waits for a Finance user to approve.
            rec._approval_request_or_proceed()
            rec.message_post(body=_("Submitted for approval."), subtype_xmlid="mail.mt_note")
        return True

    def _approval_on_granted(self):
        """Engine hook: advance to approved once every tier passes."""
        return self.action_approve()

    def action_approve(self):
        for rec in self:
            if rec.state not in ("to_approve", "draft"):
                raise UserError(_("Only requests awaiting approval can be approved."))
            rec._approval_check_required()
            rec.state = "approved"
            rec.message_post(body=_("Approved."), subtype_xmlid="mail.mt_note")
        return True

    def action_reject(self):
        for rec in self:
            rec.action_cancel_approval()
            rec.state = "draft"
            rec.message_post(body=_("Sent back to draft."), subtype_xmlid="mail.mt_note")
        return True

    # ------------------------------------------------------------------
    # Workflow — disbursement (Bank Out)
    # ------------------------------------------------------------------
    def action_disburse(self):
        self.ensure_one()
        if self.state != "approved":
            raise UserError(_("Approve the request before disbursing."))
        amount = self.amount_requested
        if amount <= 0:
            raise UserError(_("Nothing to disburse."))
        advance = self._pc_advance_account()
        journal = self._pc_bank_journal()
        partner = self._pc_employee_partner()

        via_payment = self.env["ir.config_parameter"].sudo().get_param("custom_petty_cash.disburse_via_payment") in (
            "1",
            "true",
            "True",
        )
        if via_payment:
            move = self._disburse_via_payment(amount, advance, journal, partner)
        else:
            move = self._disburse_via_entry(amount, advance, journal, partner)

        self.disburse_move_id = move.id
        if not self.realization_deadline:
            days = int(self.env["ir.config_parameter"].sudo().get_param("custom_petty_cash.realization_days") or 14)
            self.realization_deadline = fields.Date.context_today(self) + timedelta(days=days)
        self.state = "disbursed"
        self.message_post(
            body=_("Disbursed %(amt)s via %(journal)s (entry %(move)s).")
            % {"amt": amount, "journal": journal.name, "move": move.name},
            subtype_xmlid="mail.mt_note",
        )
        return self._action_open_move(move)

    def _disburse_via_entry(self, amount, advance, journal, partner):
        self.ensure_one()
        bank_account = journal.default_account_id
        if not bank_account:
            raise UserError(_("Journal %s has no bank/cash account configured.") % journal.name)
        label = _("Petty Cash disbursement — %s") % self.name
        move = (
            self.env["account.move"]
            .with_company(self.company_id)
            .create(
                {
                    "move_type": "entry",
                    "journal_id": journal.id,
                    "date": fields.Date.context_today(self),
                    "ref": self.name,
                    "petty_cash_request_id": self.id,
                    "line_ids": [
                        fields.Command.create(
                            self._pc_line_analytic(
                                {
                                    "name": label,
                                    "account_id": advance.id,
                                    "partner_id": partner.id,
                                    "debit": amount,
                                    "credit": 0.0,
                                }
                            )
                        ),
                        fields.Command.create(
                            {
                                "name": label,
                                "account_id": bank_account.id,
                                "partner_id": partner.id,
                                "debit": 0.0,
                                "credit": amount,
                            }
                        ),
                    ],
                }
            )
        )
        move.action_post()
        return move

    def _disburse_via_payment(self, amount, advance, journal, partner):
        self.ensure_one()
        payment = (
            self.env["account.payment"]
            .with_company(self.company_id)
            .create(
                {
                    "payment_type": "outbound",
                    "partner_type": "supplier",
                    "partner_id": partner.id,
                    "amount": amount,
                    "currency_id": self.currency_id.id,
                    "journal_id": journal.id,
                    "destination_account_id": advance.id,
                    "date": fields.Date.context_today(self),
                    "memo": self.name,
                }
            )
        )
        payment.action_post()
        move = payment.move_id
        move.petty_cash_request_id = self.id
        return move

    # ------------------------------------------------------------------
    # Realization
    # ------------------------------------------------------------------
    def action_open_realization(self):
        self.ensure_one()
        if self.state not in ("disbursed", "in_realization"):
            raise UserError(_("Disburse the request before recording a realization."))
        return {
            "type": "ir.actions.act_window",
            "name": _("New Realization"),
            "res_model": "petty.cash.realization",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_request_id": self.id,
                "default_employee_id": self.employee_id.id,
                "default_company_id": self.company_id.id,
            },
        }

    def _on_realization_posted(self):
        """Called by a realization when it posts — flip to in_realization."""
        for rec in self:
            if rec.state == "disbursed":
                rec.state = "in_realization"

    # ------------------------------------------------------------------
    # Return / reimburse / settle
    # ------------------------------------------------------------------
    def action_return_balance(self):
        """Employee returns the outstanding cash: Dr Bank / Cr Advance."""
        self.ensure_one()
        outstanding = self.amount_outstanding
        if outstanding <= 0:
            raise UserError(_("There is no cash to return on this request."))
        move = self._book_bank_advance_transfer(outstanding, direction="return")
        self.message_post(
            body=_("Returned %(amt)s to the bank (entry %(move)s).") % {"amt": outstanding, "move": move.name},
            subtype_xmlid="mail.mt_note",
        )
        return self._action_open_move(move)

    def action_reimburse_shortfall(self):
        """Company reimburses over-spend: Dr Advance / Cr Bank."""
        self.ensure_one()
        outstanding = self.amount_outstanding
        if outstanding >= 0:
            raise UserError(_("There is no shortfall to reimburse on this request."))
        move = self._book_bank_advance_transfer(-outstanding, direction="reimburse")
        self.message_post(
            body=_("Reimbursed %(amt)s to the employee (entry %(move)s).") % {"amt": -outstanding, "move": move.name},
            subtype_xmlid="mail.mt_note",
        )
        return self._action_open_move(move)

    def _book_bank_advance_transfer(self, amount, direction):
        self.ensure_one()
        advance = self._pc_advance_account()
        journal = self._pc_bank_journal()
        partner = self._pc_employee_partner()
        bank_account = journal.default_account_id
        if not bank_account:
            raise UserError(_("Journal %s has no bank/cash account configured.") % journal.name)
        if direction == "return":
            label = _("Petty Cash return — %s") % self.name
            advance_dr, advance_cr = 0.0, amount
            bank_dr, bank_cr = amount, 0.0
        else:
            label = _("Petty Cash reimbursement — %s") % self.name
            advance_dr, advance_cr = amount, 0.0
            bank_dr, bank_cr = 0.0, amount
        move = (
            self.env["account.move"]
            .with_company(self.company_id)
            .create(
                {
                    "move_type": "entry",
                    "journal_id": journal.id,
                    "date": fields.Date.context_today(self),
                    "ref": self.name,
                    "petty_cash_request_id": self.id,
                    "line_ids": [
                        fields.Command.create(
                            self._pc_line_analytic(
                                {
                                    "name": label,
                                    "account_id": advance.id,
                                    "partner_id": partner.id,
                                    "debit": advance_dr,
                                    "credit": advance_cr,
                                }
                            )
                        ),
                        fields.Command.create(
                            {
                                "name": label,
                                "account_id": bank_account.id,
                                "partner_id": partner.id,
                                "debit": bank_dr,
                                "credit": bank_cr,
                            }
                        ),
                    ],
                }
            )
        )
        move.action_post()
        return move

    def action_settle(self):
        self.ensure_one()
        if self.state not in ("disbursed", "in_realization"):
            raise UserError(_("Only disbursed requests can be settled."))
        if not self.currency_id.is_zero(self.amount_outstanding):
            raise UserError(
                _(
                    "The advance is not cleared yet (outstanding %(amt)s). Record the "
                    "remaining realizations, a return, or a reimbursement first.",
                    amt=self.amount_outstanding,
                )
            )
        # Reconcile the advance-account lines so the ledger closes for this request.
        adv_lines = self._advance_move_lines(posted_only=True).filtered(lambda l: not l.reconciled)
        if adv_lines and adv_lines.mapped("account_id").reconcile:
            try:
                adv_lines.reconcile()
            except UserError:
                # Non-fatal: settlement stands even if auto-reconcile can't match.
                pass
        self.state = "settled"
        self.message_post(body=_("Settled — advance cleared."), subtype_xmlid="mail.mt_note")
        return True

    # ------------------------------------------------------------------
    # Cancel / reset
    # ------------------------------------------------------------------
    def action_cancel(self):
        for rec in self:
            if rec.state == "settled":
                raise UserError(_("Cannot cancel a settled request."))
            if rec.move_ids.filtered(lambda m: m.state == "posted"):
                raise UserError(_("This request already has posted journal entries. Reverse them before cancelling."))
            rec.action_cancel_approval()
            rec.state = "cancelled"
        return True

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state not in ("cancelled", "to_approve"):
                raise UserError(_("Only cancelled or awaiting-approval requests can be reset."))
            rec.action_cancel_approval()
            rec.state = "draft"
        return True

    # ------------------------------------------------------------------
    # Smart buttons
    # ------------------------------------------------------------------
    def _action_open_move(self, move):
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": move.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_moves(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Journal Entries"),
            "res_model": "account.move",
            "domain": [("petty_cash_request_id", "=", self.id)],
            "view_mode": "list,form",
        }

    def action_view_realizations(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Realizations"),
            "res_model": "petty.cash.realization",
            "domain": [("request_id", "=", self.id)],
            "view_mode": "list,form",
            "context": {"default_request_id": self.id, "default_employee_id": self.employee_id.id},
        }

    # ------------------------------------------------------------------
    # Cron — overdue reminder
    # ------------------------------------------------------------------
    @api.model
    def _cron_realization_reminder(self):
        today = fields.Date.context_today(self)
        overdue = self.search(
            [
                ("state", "in", ("disbursed", "in_realization")),
                ("realization_deadline", "<", today),
            ]
        )
        template = self.env.ref("custom_petty_cash.mail_template_realization_reminder", raise_if_not_found=False)
        activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        for rec in overdue:
            if rec.currency_id.is_zero(rec.amount_outstanding):
                continue
            user = rec.employee_id.user_id
            if (
                user
                and activity_type
                and not rec.activity_ids.filtered(lambda a: a.activity_type_id == activity_type and a.user_id == user)
            ):
                rec.activity_schedule(
                    "mail.mail_activity_data_todo",
                    user_id=user.id,
                    summary=_("Petty cash realization overdue"),
                    note=_("Please submit the realization for %s.") % rec.name,
                )
            if template and user:
                template.send_mail(rec.id, force_send=False)
        return True
