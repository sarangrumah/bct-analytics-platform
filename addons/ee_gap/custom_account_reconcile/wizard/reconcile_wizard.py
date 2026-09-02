# -*- coding: utf-8 -*-
from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class CustomAccountReconcileWizard(models.TransientModel):
    """Manual reconciliation wizard (CE stand-in for the EE one).

    Fed by the ``Reconcile`` action on selected journal items. Balanced
    selections reconcile directly; unbalanced ones either reconcile
    partially (core behaviour — the residual stays open) or post a
    write-off entry first so the whole selection fully nets out.
    """

    _name = "custom.account.reconcile.wizard"
    _description = "Reconcile Journal Items"

    line_ids = fields.Many2many("account.move.line", string="Journal Items", readonly=True)
    account_id = fields.Many2one("account.account", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id")
    debit = fields.Monetary(readonly=True, currency_field="currency_id")
    credit = fields.Monetary(readonly=True, currency_field="currency_id")
    residual = fields.Monetary(
        string="Residual",
        readonly=True,
        currency_field="currency_id",
        help="Net open amount of the selection (company currency). Zero "
        "reconciles fully; otherwise choose partial or write-off.",
    )
    is_balanced = fields.Boolean(readonly=True)

    mode = fields.Selection(
        [
            ("partial", "Partial — keep the residual open"),
            ("writeoff", "Write-off — book the residual and close fully"),
        ],
        default="partial",
        required=True,
        string="Unbalanced Handling",
    )
    writeoff_account_id = fields.Many2one(
        "account.account",
        string="Write-off Account",
        check_company=True,
        domain="[('account_type', 'not in', ('asset_receivable', 'liability_payable'))]",
    )
    writeoff_journal_id = fields.Many2one(
        "account.journal",
        string="Write-off Journal",
        check_company=True,
        domain="[('type', '=', 'general')]",
    )
    writeoff_date = fields.Date(string="Write-off Date", default=fields.Date.context_today)
    writeoff_label = fields.Char(string="Label", default="Write-Off")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get("active_model") != "account.move.line":
            return res
        lines = self.env["account.move.line"].browse(self.env.context.get("active_ids", []))
        lines = lines.filtered(lambda l: not l.reconciled)
        if len(lines) < 2:
            raise UserError(_("Select at least two unreconciled journal items."))
        if any(l.parent_state != "posted" for l in lines):
            raise UserError(_("Only journal items of posted entries can be reconciled."))
        accounts = lines.mapped("account_id")
        if len(accounts) > 1:
            raise UserError(
                _(
                    "All items must share one account (found: %s). Reconciliation "
                    "matches debits and credits inside a single account."
                )
                % ", ".join(accounts.mapped("display_name"))
            )
        if not accounts.reconcile:
            raise UserError(
                _(
                    "Account %s does not allow reconciliation. Enable 'Allow "
                    "Reconciliation' on it first (Chart of Accounts)."
                )
                % accounts.display_name
            )
        companies = lines.mapped("company_id")
        if len(companies) > 1:
            raise UserError(_("All items must belong to the same company."))
        residual = sum(lines.mapped("amount_residual"))
        res.update(
            {
                "line_ids": [Command.set(lines.ids)],
                "account_id": accounts.id,
                "company_id": companies.id,
                "debit": sum(lines.mapped("debit")),
                "credit": sum(lines.mapped("credit")),
                "residual": residual,
                "is_balanced": companies.currency_id.is_zero(residual),
            }
        )
        return res

    def action_reconcile(self):
        self.ensure_one()
        lines = self.line_ids
        if not self.is_balanced and self.mode == "writeoff":
            lines |= self._create_writeoff_line()
        lines.reconcile()
        return {"type": "ir.actions.act_window_close"}

    def _create_writeoff_line(self):
        """Post the write-off entry; return its line on the reconciled account."""
        if not self.writeoff_account_id or not self.writeoff_journal_id:
            raise UserError(_("Write-off mode needs both a write-off account and a journal."))
        if any(l.currency_id != self.currency_id for l in self.line_ids):
            raise UserError(
                _(
                    "Write-off is only supported for company-currency items. "
                    "Foreign-currency lines: use partial mode and clear the "
                    "residual via a manual entry."
                )
            )
        residual = self.residual
        partners = self.line_ids.mapped("partner_id")
        partner_id = partners.id if len(partners) == 1 else False
        label = self.writeoff_label or _("Write-Off")
        move = self.env["account.move"].create(
            {
                "journal_id": self.writeoff_journal_id.id,
                "date": self.writeoff_date or fields.Date.context_today(self),
                "ref": label,
                "line_ids": [
                    # Nets the open residual off the reconciled account…
                    Command.create(
                        {
                            "name": label,
                            "account_id": self.account_id.id,
                            "partner_id": partner_id,
                            "debit": -residual if residual < 0 else 0.0,
                            "credit": residual if residual > 0 else 0.0,
                        }
                    ),
                    # …against the write-off account.
                    Command.create(
                        {
                            "name": label,
                            "account_id": self.writeoff_account_id.id,
                            "partner_id": partner_id,
                            "debit": residual if residual > 0 else 0.0,
                            "credit": -residual if residual < 0 else 0.0,
                        }
                    ),
                ],
            }
        )
        move.action_post()
        return move.line_ids.filtered(lambda l: l.account_id == self.account_id)
