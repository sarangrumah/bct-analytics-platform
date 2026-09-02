# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


def _month_ends(start, end):
    """Month-end dates covering [start, end], e.g. Jan 15→Mar 10 gives
    Jan 31, Feb 28/29, Mar 31 — one recognition entry per month."""
    ends = []
    cursor = start.replace(day=1)
    while cursor <= end:
        month_end = cursor + relativedelta(months=1, days=-1)
        ends.append(month_end)
        cursor += relativedelta(months=1)
    return ends


class AccountMove(models.Model):
    _inherit = "account.move"

    deferred_origin_move_id = fields.Many2one(
        "account.move",
        string="Deferred Origin",
        readonly=True,
        copy=False,
        index="btree_not_null",
        help="Invoice/bill this deferral or recognition entry was generated from.",
    )
    deferred_entry_type = fields.Selection(
        [("deferral", "Deferral"), ("recognition", "Recognition")],
        readonly=True,
        copy=False,
    )
    deferred_generated_ids = fields.One2many(
        "account.move", "deferred_origin_move_id", string="Generated Deferred Entries"
    )
    deferred_generated_count = fields.Integer(compute="_compute_deferred_generated_count")

    @api.depends("deferred_generated_ids")
    def _compute_deferred_generated_count(self):
        for move in self:
            move.deferred_generated_count = len(move.deferred_generated_ids)

    def action_open_deferred_entries(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Deferred Entries"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("deferred_origin_move_id", "=", self.id)],
            "context": {"create": 0},
        }

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------
    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        for move in posted:
            if move.is_invoice(include_receipts=True) and not move.deferred_entry_type:
                move._generate_deferred_entries()
        return posted

    def _deferred_config(self):
        company = self.company_id
        return {
            "expense_account": company.deferred_expense_account_id,
            "revenue_account": company.deferred_revenue_account_id,
            "journal": company.deferred_journal_id,
        }

    def _generate_deferred_entries(self):
        self.ensure_one()
        if self.deferred_generated_ids:
            return  # idempotent — e.g. draft→post→draft→post cycles
        lines = self.invoice_line_ids.filtered(lambda l: l._is_deferrable())
        if not lines:
            return
        cfg = self._deferred_config()
        if not cfg["journal"]:
            raise UserError(_("Configure the Deferred journal in Settings → Accounting first."))

        deferral_lines = []  # accumulated Command.create vals for the deferral move
        # recognition buckets: (month_end) -> list of vals
        recognition = {}
        today = fields.Date.context_today(self)

        for line in lines:
            deferred_acc = (
                cfg["expense_account"] if line.account_id.internal_group == "expense" else cfg["revenue_account"]
            )
            if not deferred_acc:
                raise UserError(
                    _("Configure the Deferred %s account in Settings → Accounting first (needed by line '%s').")
                    % (line.account_id.internal_group, line.name or line.account_id.code)
                )
            balance = line.balance  # expense: debit>0; revenue: credit>0 (balance<0)
            if self.currency_id.is_zero(balance):
                continue

            # 1) Deferral: move the FULL amount off the P&L account.
            deferral_lines += [
                Command.create(
                    {
                        "name": _("Deferral of: %s") % (line.name or ""),
                        "account_id": line.account_id.id,
                        "partner_id": line.partner_id.id,
                        "debit": -balance if balance < 0 else 0.0,
                        "credit": balance if balance > 0 else 0.0,
                    }
                ),
                Command.create(
                    {
                        "name": _("Deferral of: %s") % (line.name or ""),
                        "account_id": deferred_acc.id,
                        "partner_id": line.partner_id.id,
                        "debit": balance if balance > 0 else 0.0,
                        "credit": -balance if balance < 0 else 0.0,
                    }
                ),
            ]

            # 2) Recognitions: day-count prorated per month, remainder last.
            start, end = line.deferred_start_date, line.deferred_end_date
            total_days = (end - start).days + 1
            month_ends = _month_ends(start, end)
            allocated = 0.0
            for i, m_end in enumerate(month_ends):
                m_start = max(start, m_end.replace(day=1))
                m_stop = min(end, m_end)
                if i == len(month_ends) - 1:
                    amount = self.currency_id.round(balance - allocated)
                else:
                    days = (m_stop - m_start).days + 1
                    amount = self.currency_id.round(balance * days / total_days)
                    allocated += amount
                if self.currency_id.is_zero(amount):
                    continue
                recognition.setdefault(m_end, []).extend(
                    [
                        Command.create(
                            {
                                "name": _("Recognition %s: %s") % (m_end, line.name or ""),
                                "account_id": deferred_acc.id,
                                "partner_id": line.partner_id.id,
                                "debit": -amount if amount < 0 else 0.0,
                                "credit": amount if amount > 0 else 0.0,
                            }
                        ),
                        Command.create(
                            {
                                "name": _("Recognition %s: %s") % (m_end, line.name or ""),
                                "account_id": line.account_id.id,
                                "partner_id": line.partner_id.id,
                                "debit": amount if amount > 0 else 0.0,
                                "credit": -amount if amount < 0 else 0.0,
                            }
                        ),
                    ]
                )

        if not deferral_lines:
            return

        Move = self.env["account.move"].with_context(skip_invoice_sync=True)
        deferral = Move.create(
            {
                "move_type": "entry",
                "journal_id": cfg["journal"].id,
                "date": self.date,
                "ref": _("Deferral of %s") % (self.name or ""),
                "deferred_origin_move_id": self.id,
                "deferred_entry_type": "deferral",
                "line_ids": deferral_lines,
            }
        )
        deferral._post(soft=False)

        for m_end in sorted(recognition):
            move = Move.create(
                {
                    "move_type": "entry",
                    "journal_id": cfg["journal"].id,
                    "date": m_end,
                    "ref": _("Deferred recognition %s of %s") % (m_end, self.name or ""),
                    "deferred_origin_move_id": self.id,
                    "deferred_entry_type": "recognition",
                    "line_ids": recognition[m_end],
                }
            )
            if m_end <= today:
                move._post(soft=False)
            else:
                move.auto_post = "at_date"

    # ------------------------------------------------------------------
    # Cleanup on reset/reversal
    # ------------------------------------------------------------------
    def button_draft(self):
        res = super().button_draft()
        for move in self:
            generated = move.deferred_generated_ids
            if generated:
                # Surface lock-date errors — never bypass them.
                generated.filtered(lambda m: m.state == "posted").button_draft()
                generated.with_context(force_delete=False).unlink()
        return res
