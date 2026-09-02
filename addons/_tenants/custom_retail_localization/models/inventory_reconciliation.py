# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_is_zero


class RetailInventoryReconciliation(models.Model):
    """Periodic manual journal to reconcile GL inventory to actual stock value.

    In this Odoo 19 setup goods receipts/deliveries do not post inventory
    journals (valuation accounts live on stock locations, which are unset), so
    the GL inventory-asset accounts drift from the real on-hand value carried on
    ``stock.quant.value`` / ``product.total_value``. This tool computes, per
    stock-valuation account, the actual stock value vs the current GL balance
    and produces a DRAFT ``account.move`` booking the difference against an
    inventory-variation account for the accountant to review and post.
    """

    _name = "retail.inventory.reconciliation"
    _description = "Periodic Inventory Reconciliation"
    _order = "date desc, id desc"

    name = fields.Char(default="/", copy=False, readonly=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    date = fields.Date(
        required=True,
        default=fields.Date.context_today,
        help="Reconcile the GL as of this date (uses posted entries up to it).",
    )
    journal_id = fields.Many2one(
        "account.journal",
        string="Journal",
        required=True,
        domain="[('type', '=', 'general'), ('company_id', '=', company_id)]",
    )
    counterpart_account_id = fields.Many2one(
        "account.account",
        string="Inventory Variation Account",
        domain="[('company_ids', 'in', company_id)]",
        help="P&L / adjustment account the reconciliation difference is booked "
        "against. Defaults per line to the product category's Stock Variation "
        "account when it is set.",
    )
    line_ids = fields.One2many("retail.inventory.reconciliation.line", "reconciliation_id", copy=False)
    move_id = fields.Many2one("account.move", readonly=True, copy=False)
    state = fields.Selection(
        [("draft", "Draft"), ("computed", "Computed"), ("generated", "Generated")],
        default="draft",
        copy=False,
    )
    total_difference = fields.Monetary(compute="_compute_total_difference", currency_field="currency_id")
    currency_id = fields.Many2one(related="company_id.currency_id")

    @api.depends("line_ids.difference")
    def _compute_total_difference(self):
        for rec in self:
            rec.total_difference = sum(rec.line_ids.mapped("difference"))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("retail.inventory.reconciliation") or "/"
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Computation
    # ------------------------------------------------------------------
    def _get_stock_value_by_account(self):
        """Actual on-hand value grouped by stock-valuation account.

        :return: dict {account.account: (value, variation_account or False)}
        """
        self.ensure_one()
        company = self.company_id
        Quant = self.env["stock.quant"].with_company(company)
        domain = [
            ("company_id", "=", company.id),
            ("location_id.usage", "=", "internal"),
            ("quantity", "!=", 0),
        ]
        # Per-product on-hand value (stock.quant.value is aggregatable).
        grouped = Quant._read_group(domain, ["product_id"], ["value:sum"])
        result = {}
        for product, value in grouped:
            if not value:
                continue
            categ = product.categ_id
            acc = categ.with_company(company).property_stock_valuation_account_id
            if not acc:
                continue
            var = categ.with_company(company).account_stock_variation_id
            cur_val, cur_var = result.get(acc, (0.0, False))
            result[acc] = (cur_val + value, cur_var or var)
        return result

    def _get_gl_balance(self, accounts):
        """Current posted GL balance per account up to ``self.date``."""
        self.ensure_one()
        if not accounts:
            return {}
        domain = [
            ("company_id", "=", self.company_id.id),
            ("account_id", "in", accounts.ids),
            ("parent_state", "=", "posted"),
            ("date", "<=", self.date),
        ]
        grouped = self.env["account.move.line"]._read_group(domain, ["account_id"], ["balance:sum"])
        return {account.id: balance for account, balance in grouped}

    def action_compute(self):
        for rec in self:
            if rec.move_id:
                raise UserError(_("A journal entry was already generated for %s.", rec.name))
            rec.line_ids.unlink()
            stock_by_acc = rec._get_stock_value_by_account()
            accounts = self.env["account.account"].browse([a.id for a in stock_by_acc])
            gl_by_acc = rec._get_gl_balance(accounts)
            lines = []
            for account, (stock_value, var_account) in stock_by_acc.items():
                lines.append(
                    (
                        0,
                        0,
                        {
                            "account_id": account.id,
                            "counterpart_account_id": (
                                var_account.id if var_account else rec.counterpart_account_id.id
                            ),
                            "book_value": gl_by_acc.get(account.id, 0.0),
                            "stock_value": stock_value,
                        },
                    )
                )
            rec.line_ids = lines
            rec.state = "computed"
        return True

    # ------------------------------------------------------------------
    # Journal generation
    # ------------------------------------------------------------------
    def action_generate_move(self):
        self.ensure_one()
        if self.move_id:
            raise UserError(_("A journal entry was already generated."))
        if self.state == "draft":
            self.action_compute()
        currency = self.company_id.currency_id
        move_lines = []
        for line in self.line_ids:
            if float_is_zero(line.difference, precision_rounding=currency.rounding):
                continue
            counterpart = line.counterpart_account_id or self.counterpart_account_id
            if not counterpart:
                raise UserError(
                    _(
                        "No inventory-variation account set for %(acc)s. Set one "
                        "on the line, on the reconciliation, or as the category's "
                        "Stock Variation account.",
                        acc=line.account_id.display_name,
                    )
                )
            diff = line.difference  # stock - book ; >0 => increase inventory
            label = _("Inventory reconciliation %s", self.name)
            move_lines.append(
                (
                    0,
                    0,
                    {
                        "account_id": line.account_id.id,
                        "name": label,
                        "debit": diff if diff > 0 else 0.0,
                        "credit": -diff if diff < 0 else 0.0,
                    },
                )
            )
            move_lines.append(
                (
                    0,
                    0,
                    {
                        "account_id": counterpart.id,
                        "name": label,
                        "debit": -diff if diff < 0 else 0.0,
                        "credit": diff if diff > 0 else 0.0,
                    },
                )
            )
        if not move_lines:
            raise UserError(_("Nothing to reconcile — GL already matches the stock value."))
        move = self.env["account.move"].create(
            {
                "move_type": "entry",
                "journal_id": self.journal_id.id,
                "company_id": self.company_id.id,
                "date": self.date,
                "ref": _("Inventory reconciliation %s", self.name),
                "line_ids": move_lines,
            }
        )
        self.move_id = move.id
        self.state = "generated"
        return self.action_view_move()

    def action_view_move(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.move_id.id,
            "view_mode": "form",
            "target": "current",
        }

    @api.model
    def _cron_generate_drafts(self):
        """Create one computed reconciliation per company, generating a DRAFT
        journal entry when there is a difference. Never posts automatically."""
        for company in self.env["res.company"].search([]):
            journal = self.env["account.journal"].search(
                [("type", "=", "general"), ("company_id", "=", company.id)],
                limit=1,
            )
            if not journal:
                continue
            rec = self.with_company(company).create({"company_id": company.id, "journal_id": journal.id})
            rec.action_compute()
            currency = company.currency_id
            if any(not float_is_zero(line.difference, precision_rounding=currency.rounding) for line in rec.line_ids):
                try:
                    rec.action_generate_move()
                except UserError:
                    continue
        return True


class RetailInventoryReconciliationLine(models.Model):
    _name = "retail.inventory.reconciliation.line"
    _description = "Periodic Inventory Reconciliation Line"

    reconciliation_id = fields.Many2one("retail.inventory.reconciliation", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="reconciliation_id.company_id", store=True)
    currency_id = fields.Many2one(related="reconciliation_id.currency_id")
    account_id = fields.Many2one("account.account", string="Valuation Account", required=True)
    counterpart_account_id = fields.Many2one("account.account", string="Variation Account")
    book_value = fields.Monetary(string="GL Balance", currency_field="currency_id")
    stock_value = fields.Monetary(string="Stock Value", currency_field="currency_id")
    difference = fields.Monetary(compute="_compute_difference", store=True, currency_field="currency_id")

    @api.depends("stock_value", "book_value")
    def _compute_difference(self):
        for line in self:
            line.difference = line.stock_value - line.book_value
