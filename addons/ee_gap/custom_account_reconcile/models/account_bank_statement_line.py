# -*- coding: utf-8 -*-
from odoo import Command, _, models
from odoo.exceptions import UserError


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    # ------------------------------------------------------------------
    # Candidate search (CE has no matching engine — this is ours)
    # ------------------------------------------------------------------
    def _get_match_candidates(self, limit=30, relax=False):
        """Score-ordered AMLs this statement line could settle.

        Tiers: exact residual + partner + ref-substring, then exact residual,
        then partner match, then same-sign amount proximity. ``relax`` widens
        the net (drops the amount filter) for the wizard's "Search more".
        """
        self.ensure_one()
        Aml = self.env["account.move.line"]
        domain = self._get_default_amls_matching_domain()
        # Money-in settles open debits (residual > 0), money-out open credits.
        if not relax:
            sign_leaf = ("amount_residual", ">", 0) if self.amount > 0 else ("amount_residual", "<", 0)
            domain = domain + [sign_leaf]
        candidates = Aml.search(domain, limit=limit * 10, order="date desc, id desc")

        target = abs(self.amount)
        comp_cur = self.company_id.currency_id
        ref = (self.payment_ref or "").strip().lower()

        def score(aml):
            s = 0.0
            if not comp_cur.compare_amounts(abs(aml.amount_residual), target):
                s += 100.0
            if self.partner_id and aml.partner_id == self.partner_id:
                s += 50.0
            if ref and aml.move_id.name and aml.move_id.name.lower() in ref:
                s += 40.0
            if ref and aml.name and aml.name.strip().lower() and aml.name.strip().lower() in ref:
                s += 20.0
            # closeness tie-breaker
            if target:
                s += max(0.0, 10.0 - abs(abs(aml.amount_residual) - target) / target * 10.0)
            return s

        return candidates.sorted(key=score, reverse=True)[:limit]

    def _get_auto_match_candidate(self):
        """The single unambiguous candidate, or empty. Auto-match only fires on
        an exact-residual hit that is unique (with partner agreement when the
        statement line carries a partner)."""
        self.ensure_one()
        comp_cur = self.company_id.currency_id
        target = abs(self.amount)
        exact = self._get_match_candidates(limit=10).filtered(
            lambda aml: (
                not comp_cur.compare_amounts(abs(aml.amount_residual), target)
                and (not self.partner_id or aml.partner_id == self.partner_id)
            )
        )
        return exact if len(exact) == 1 else exact.browse()

    # ------------------------------------------------------------------
    # Reconcile mechanics
    # ------------------------------------------------------------------
    def _reconcile_with_amls(self, amls, writeoff_vals=None):
        """Replace this line's suspense leg with counterparts of ``amls``
        (full residual each), plus an optional write-off leg; the unmatched
        remainder stays on suspense. Then reconcile pair-wise.

        :param amls: account.move.line recordset to settle.
        :param writeoff_vals: optional dict(account_id, name) absorbing the
            remainder instead of leaving it on suspense.
        """
        self.ensure_one()
        if self.is_reconciled:
            raise UserError(_("Statement line %s is already reconciled.") % self.display_name)
        liquidity, suspense, other = self._seek_for_lines()
        if not suspense:
            raise UserError(
                _("Statement line %s has no suspense leg to replace — undo its current matching first.")
                % self.display_name
            )
        suspense_balance = sum(suspense.mapped("balance"))

        commands = [Command.delete(line.id) for line in suspense]
        matched = []
        remainder = suspense_balance
        for aml in amls:
            if aml.reconciled:
                raise UserError(_("%s is already reconciled.") % aml.display_name)
            # The counterpart must oppose the AML's residual to net it out.
            amounts = self._prepare_counterpart_amounts_using_st_line_rate(
                aml.currency_id, -aml.amount_residual, -aml.amount_residual_currency
            )
            commands.append(
                Command.create(
                    {
                        "name": aml.move_id.name or aml.name or self.payment_ref or "/",
                        "account_id": aml.account_id.id,
                        "partner_id": aml.partner_id.id,
                        "currency_id": aml.currency_id.id,
                        "amount_currency": amounts["amount_currency"],
                        "balance": amounts["balance"],
                    }
                )
            )
            matched.append(aml)
            remainder -= amounts["balance"]

        comp_cur = self.company_id.currency_id
        if not comp_cur.is_zero(remainder):
            if writeoff_vals:
                commands.append(
                    Command.create(
                        {
                            "name": writeoff_vals.get("name") or _("Write-Off"),
                            "account_id": writeoff_vals["account_id"],
                            "partner_id": self.partner_id.id,
                            "balance": remainder,
                        }
                    )
                )
            else:
                # Leave the open remainder on suspense (partial match).
                commands.append(
                    Command.create(
                        {
                            "name": self.payment_ref or _("Open balance"),
                            "account_id": self.journal_id.suspense_account_id.id,
                            "balance": remainder,
                        }
                    )
                )

        before_ids = set(self.move_id.line_ids.ids)
        self.move_id.with_context(
            force_delete=True, skip_readonly_check=True, skip_account_move_synchronization=True
        ).write({"line_ids": commands})
        new_lines = self.move_id.line_ids.filtered(lambda l: l.id not in before_ids).sorted("id")

        # Creation order follows command order: first len(matched) lines are
        # the counterparts, pair each with its AML and reconcile.
        for aml, counterpart in zip(matched, new_lines, strict=False):
            if counterpart.account_id.reconcile or counterpart.account_id.account_type in (
                "asset_receivable",
                "liability_payable",
            ):
                (aml + counterpart).reconcile()
        return True

    # ------------------------------------------------------------------
    # UI entry points
    # ------------------------------------------------------------------
    def action_open_match_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Match: %s") % (self.payment_ref or self.display_name),
            "res_model": "custom.bank.reconcile.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_st_line_id": self.id},
        }

    def action_auto_match(self):
        """Silently reconcile every selected line that has one unambiguous
        exact candidate; report the tally."""
        matched = skipped = 0
        for line in self.filtered(lambda l: not l.is_reconciled):
            aml = line._get_auto_match_candidate()
            if aml:
                line._reconcile_with_amls(aml)
                matched += 1
            else:
                skipped += 1
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if matched else "warning",
                "message": _("%s matched, %s skipped (no unambiguous candidate).") % (matched, skipped),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
