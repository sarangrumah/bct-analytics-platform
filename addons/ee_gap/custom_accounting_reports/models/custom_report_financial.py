# -*- coding: utf-8 -*-
"""Hierarchical, configurable financial-report tree.

Equivalent of CE/EE ``account.financial.report``: declarative tree
nodes that aggregate accounts or account types and feed into Balance
Sheet / P&L / Cash Flow renderers. Editable at runtime via
Configuration → Financial Report Trees.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CustomReportFinancial(models.Model):
    _name = "custom.report.financial"
    _description = "Custom Financial Report Tree"
    _order = "sequence, id"
    _parent_store = True

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        required=True,
        help="Stable identifier referenced by reports and XML data.",
    )
    sequence = fields.Integer(default=10)
    parent_id = fields.Many2one(
        comodel_name="custom.report.financial",
        string="Parent",
        ondelete="cascade",
        index=True,
    )
    parent_path = fields.Char(index=True)
    children_ids = fields.One2many(
        comodel_name="custom.report.financial",
        inverse_name="parent_id",
        string="Children",
    )

    category = fields.Selection(
        selection=[
            ("balance_sheet", "Balance Sheet"),
            ("profit_loss", "Profit & Loss"),
            ("cash_flow", "Cash Flow"),
            ("custom", "Custom"),
        ],
        default="custom",
        required=True,
        help="Top-level grouping; child nodes inherit the root's value.",
    )
    type = fields.Selection(
        selection=[
            ("accounts", "Sum of Selected Accounts"),
            ("account_type", "Sum of Account Types"),
            ("tags", "Sum by Tag (legacy)"),
            ("computed", "View — Sum of Children"),
        ],
        default="computed",
        required=True,
    )
    sign = fields.Integer(
        default=1,
        help="+1 keeps the natural sign, -1 flips it. Use -1 for "
        "revenue/liability/equity sections to display positively.",
    )
    style = fields.Selection(
        selection=[
            ("normal", "Normal"),
            ("header", "Header"),
            ("subtotal", "Subtotal"),
            ("total", "Total"),
        ],
        default="normal",
    )
    level = fields.Integer(default=1)
    account_ids = fields.Many2many(
        comodel_name="account.account",
        relation="custom_report_financial_account_rel",
        column1="report_id",
        column2="account_id",
        string="Accounts",
    )
    account_type_ids = fields.Char(
        string="Account Types",
        help="Comma-separated list of ``account.account.account_type`` "
        "values (e.g. ``asset_current,asset_non_current``).",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        help="Optional company scope. Empty = any.",
    )
    active = fields.Boolean(default=True)

    @api.constrains("parent_id")
    def _check_node_recursion(self):
        if not self._check_recursion():
            raise ValidationError(_("A financial report node cannot be its own ancestor."))

    @api.depends("code", "name")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"[{rec.code}] {rec.name}" if rec.code else rec.name or ""

    def get_account_type_codes(self):
        self.ensure_one()
        return [c.strip() for c in (self.account_type_ids or "").split(",") if c.strip()]

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------
    def _node_value(self, balance_cache, type_cache):
        """Recursively compute a node's signed value.

        ``balance_cache`` is ``{account_id: balance}`` and ``type_cache``
        is ``{account_id: account_type}`` so we never re-query the DB
        while walking the tree.
        """
        self.ensure_one()
        value = 0.0
        if self.type == "computed":
            for child in self.children_ids:
                value += child._node_value(balance_cache, type_cache)
        elif self.type == "accounts":
            for acc in self.account_ids:
                value += balance_cache.get(acc.id, 0.0)
        elif self.type == "account_type":
            codes = self.get_account_type_codes()
            for acc_id, balance in balance_cache.items():
                if type_cache.get(acc_id) in codes:
                    value += balance
        return value * (self.sign or 1)

    def _flatten(self, balance_cache, type_cache, lines, depth=0):
        self.ensure_one()
        value = self._node_value(balance_cache, type_cache)
        lines.append(
            {
                "type": self.style or "normal",
                "label": self.name,
                "code": self.code,
                "level": depth,
                "signed_balance": value,
                "style": self.style,
            }
        )
        if self.type == "computed":
            for child in self.children_ids:
                child._flatten(balance_cache, type_cache, lines, depth + 1)


class CustomReportFinancialRenderer(models.AbstractModel):
    """Renders any ``custom.report.financial`` tree against a period."""

    _name = "custom.report.financial.renderer"
    _inherit = "custom.report.engine"
    _description = "Custom Financial Report Renderer"

    _report_code = "financial"
    _report_title = "Financial Report"

    def _xlsx_columns(self):
        # Lines produced by ``custom.report.financial._flatten`` key on
        # ``code`` / ``label`` / ``signed_balance`` and carry ``level``, so
        # the engine's default (level-aware) flattener renders them as-is.
        return [
            {"header": "Code", "field": "code", "kind": "text", "width": 16},
            {"header": "Account", "field": "label", "kind": "text", "width": 46},
            {"header": "Balance", "field": "signed_balance", "kind": "number", "width": 20},
        ]

    def _build_lines(self, filters):
        report_id = filters.get("financial_report_id")
        if not report_id:
            return [
                {
                    "type": "warning",
                    "label": _("Select a Financial Report tree to render."),
                }
            ]
        root = self.env["custom.report.financial"].browse(report_id)
        if not root.exists():
            return [
                {
                    "type": "warning",
                    "label": _(
                        "Financial report tree %(rid)s no longer exists.",
                        rid=report_id,
                    ),
                }
            ]
        per_account = self._get_account_balances(filters)
        balance_cache = {row["account_id"]: row["balance"] for row in per_account.values()}
        type_cache = {row["account_id"]: row["account_type"] for row in per_account.values()}
        lines = []
        root._flatten(balance_cache, type_cache, lines)
        return lines
