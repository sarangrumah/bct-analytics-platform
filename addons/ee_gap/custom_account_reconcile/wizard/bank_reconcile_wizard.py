# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BankReconcileWizard(models.TransientModel):
    """Match one bank statement line against open journal items (the CE
    stand-in for the EE bank reconciliation widget)."""

    _name = "custom.bank.reconcile.wizard"
    _description = "Bank Statement Line Matching"

    st_line_id = fields.Many2one("account.bank.statement.line", required=True, readonly=True)
    company_id = fields.Many2one(related="st_line_id.company_id")
    currency_id = fields.Many2one(related="st_line_id.company_id.currency_id")
    journal_id = fields.Many2one(related="st_line_id.journal_id")
    date = fields.Date(related="st_line_id.date")
    payment_ref = fields.Char(related="st_line_id.payment_ref")
    partner_id = fields.Many2one(related="st_line_id.partner_id")
    amount = fields.Monetary(related="st_line_id.amount", currency_field="currency_id")

    candidate_ids = fields.One2many("custom.bank.reconcile.wizard.line", "wizard_id")
    selected_total = fields.Monetary(compute="_compute_amounts", currency_field="currency_id")
    remainder = fields.Monetary(
        compute="_compute_amounts",
        currency_field="currency_id",
        help="Statement amount not yet covered by the selected items. "
        "Reconciling with a remainder leaves it open on suspense unless "
        "you write it off.",
    )
    writeoff = fields.Boolean(string="Write off the remainder")
    writeoff_account_id = fields.Many2one(
        "account.account",
        check_company=True,
        domain="[('account_type', 'not in', ('asset_receivable', 'liability_payable'))]",
    )
    writeoff_label = fields.Char(default="Write-Off")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        st_line = self.env["account.bank.statement.line"].browse(
            res.get("st_line_id") or self.env.context.get("default_st_line_id")
        )
        if not st_line:
            return res
        if st_line.is_reconciled:
            raise UserError(_("This statement line is already reconciled."))
        res["candidate_ids"] = self._candidate_commands(st_line, relax=False)
        return res

    @api.model
    def _candidate_commands(self, st_line, relax):
        candidates = st_line._get_match_candidates(relax=relax)
        comp_cur = st_line.company_id.currency_id
        target = abs(st_line.amount)
        commands = []
        preselected = False
        for aml in candidates:
            exact = not comp_cur.compare_amounts(abs(aml.amount_residual), target)
            select = exact and not preselected
            preselected = preselected or select
            commands.append((0, 0, {"aml_id": aml.id, "selected": select}))
        return commands

    @api.depends("candidate_ids.selected", "amount")
    def _compute_amounts(self):
        for wiz in self:
            picked = wiz.candidate_ids.filtered("selected").mapped("aml_id")
            total = sum(picked.mapped("amount_residual"))
            wiz.selected_total = total
            # Suspense carries -amount; counterparts carry -residual each.
            wiz.remainder = -(wiz.amount or 0.0) + total

    def action_search_more(self):
        self.ensure_one()
        keep = self.candidate_ids.filtered("selected").mapped("aml_id")
        self.candidate_ids.unlink()
        commands = self._candidate_commands(self.st_line_id, relax=True)
        self.candidate_ids = [
            c if c[2]["aml_id"] not in keep.ids else (0, 0, dict(c[2], selected=True)) for c in commands
        ]
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_reconcile(self):
        self.ensure_one()
        amls = self.candidate_ids.filtered("selected").mapped("aml_id")
        if not amls and not self.writeoff:
            raise UserError(_("Select at least one journal item (or write the line off)."))
        writeoff_vals = None
        if self.writeoff:
            if not self.writeoff_account_id:
                raise UserError(_("Pick a write-off account."))
            writeoff_vals = {
                "account_id": self.writeoff_account_id.id,
                "name": self.writeoff_label or _("Write-Off"),
            }
        self.st_line_id._reconcile_with_amls(amls, writeoff_vals=writeoff_vals)
        return {"type": "ir.actions.act_window_close"}


class BankReconcileWizardLine(models.TransientModel):
    _name = "custom.bank.reconcile.wizard.line"
    _description = "Bank Matching Candidate"
    _order = "id"

    wizard_id = fields.Many2one("custom.bank.reconcile.wizard", ondelete="cascade")
    selected = fields.Boolean()
    aml_id = fields.Many2one("account.move.line", string="Journal Item", readonly=True)
    move_name = fields.Char(related="aml_id.move_id.name", string="Entry")
    date = fields.Date(related="aml_id.date")
    account_id = fields.Many2one(related="aml_id.account_id")
    partner_id = fields.Many2one(related="aml_id.partner_id")
    name = fields.Char(related="aml_id.name", string="Label")
    amount_residual = fields.Monetary(related="aml_id.amount_residual", currency_field="company_currency_id")
    company_currency_id = fields.Many2one(related="aml_id.company_currency_id")
