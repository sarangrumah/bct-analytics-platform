# -*- coding: utf-8 -*-
from odoo import api, fields, models, tools


class CustomReconcileAccount(models.Model):
    """Read-only overview of reconcilable accounts with open items.

    One row per reconcilable account that still carries posted,
    unreconciled journal items — the CE stand-in for Enterprise's manual
    reconciliation dashboard. ``id`` is the account id itself so rows stay
    stable across refreshes; record rules on ``account.move.line`` still
    apply when clicking through, but the aggregate columns are computed
    over ALL companies sharing the account (multi-company DBs: treat the
    sums as indicative, the drill-down as authoritative).
    """

    _name = "custom.reconcile.account"
    _description = "Reconciliation Overview"
    _auto = False
    _rec_name = "account_id"
    _order = "account_id"

    account_id = fields.Many2one("account.account", string="Account", readonly=True)
    line_count = fields.Integer(string="Open Items", readonly=True)
    debit = fields.Monetary(string="Debit", currency_field="currency_id", readonly=True)
    credit = fields.Monetary(string="Credit", currency_field="currency_id", readonly=True)
    residual = fields.Monetary(
        string="Residual Balance",
        currency_field="currency_id",
        readonly=True,
        help="Sum of the open items' residual amounts (company currency). "
        "Zero means everything nets out and only the matching clicks remain.",
    )
    oldest_date = fields.Date(string="Oldest Open Item", readonly=True)
    currency_id = fields.Many2one("res.currency", compute="_compute_currency_id", string="Currency")

    @api.depends_context("company")
    def _compute_currency_id(self):
        for rec in self:
            rec.currency_id = self.env.company.currency_id

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        # self._table is an internal model attribute (not user input); f-string
        # interpolation of the view name matches the repo's other _auto=False
        # report views (e.g. custom_accounting_full.followup_level).
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT
                    aa.id                    AS id,
                    aa.id                    AS account_id,
                    COUNT(ml.id)             AS line_count,
                    SUM(ml.debit)            AS debit,
                    SUM(ml.credit)           AS credit,
                    SUM(ml.amount_residual)  AS residual,
                    MIN(ml.date)             AS oldest_date
                FROM account_move_line ml
                JOIN account_account aa ON aa.id = ml.account_id
                WHERE aa.reconcile
                  AND NOT ml.reconciled
                  AND ml.parent_state = 'posted'
                GROUP BY aa.id
            )
            """
        )

    def action_open_lines(self):
        """Drill down to this account's open items, ready to reconcile."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.account_id.display_name,
            "res_model": "account.move.line",
            "view_mode": "list",
            "domain": [
                ("account_id", "=", self.account_id.id),
                ("reconciled", "=", False),
                ("parent_state", "=", "posted"),
            ],
            "context": {
                "search_default_group_by_partner": 1,
                "create": 0,
            },
        }
