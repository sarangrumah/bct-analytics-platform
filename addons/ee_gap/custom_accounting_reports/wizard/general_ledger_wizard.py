# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields, models

from ..models.custom_report_general_ledger import GL_OPTIONAL_COLUMNS


class GeneralLedgerWizard(models.TransientModel):
    _name = "custom.report.general.ledger.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "General Ledger Wizard"
    _report_code = "general_ledger"

    date_from = fields.Date(
        required=True,
        default=lambda self: date.today().replace(month=1, day=1),
    )
    date_to = fields.Date(
        required=True,
        default=lambda self: date.today(),
    )
    company_ids = fields.Many2many(
        "res.company",
        default=lambda self: self.env.companies,
    )
    journal_ids = fields.Many2many("account.journal")
    account_ids = fields.Many2many("account.account")
    posted_only = fields.Boolean(default=True)

    # ------------------------------------------------------------------
    # Excel layout
    # ------------------------------------------------------------------
    gl_layout = fields.Selection(
        [
            ("flat", "Flat (one row per line, account as column)"),
            ("grouped", "Grouped by account"),
        ],
        string="Excel Layout",
        default="flat",
        required=True,
        help="Flat = one row per journal item with the account in its own "
        "column plus the optional display columns selected below. "
        "Grouped = per-account sections (this is what the PDF always uses).",
    )

    # Optional flat columns (all on by default). Hidden unless gl_layout=flat.
    show_doc_no = fields.Boolean(string="Document No", default=True)
    show_reference = fields.Boolean(string="Reference", default=True)
    show_tax = fields.Boolean(string="Tax Code", default=True)
    # Off by default: a tax-team specific need, and this module ships to every
    # tenant — turning it on would silently widen every existing GL export.
    show_faktur_masukan = fields.Boolean(string="No. FP Masukan", default=False)
    show_faktur_keluaran = fields.Boolean(string="No. FP Keluaran", default=False)
    show_clearing = fields.Boolean(string="Clearing Doc", default=True)
    show_cost_center = fields.Boolean(string="Cost Center", default=True)
    show_profit_center = fields.Boolean(string="Profit Center", default=True)
    show_currency = fields.Boolean(string="Doc Currency", default=True)
    show_amount_currency = fields.Boolean(string="Amount (Doc Curr.)", default=True)
    show_due_date = fields.Boolean(string="Due Date", default=True)
    show_user = fields.Boolean(string="User", default=True)

    def _selected_columns(self):
        """Return the list of enabled optional flat-column keys."""
        self.ensure_one()
        return [key for key in GL_OPTIONAL_COLUMNS if getattr(self, "show_%s" % key)]

    def _report_context_extra(self):
        """On-screen GL always uses the flat layout (a per-account nested
        table does not map to a single column set); honour the user's
        optional-column selection."""
        self.ensure_one()
        return {"gl_layout": "flat", "gl_columns": self._selected_columns()}

    def _build_filters(self):
        self.ensure_one()
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "journal_ids": self.journal_ids.ids,
            "account_ids": self.account_ids.ids,
            "posted_only": self.posted_only,
        }

    def action_print(self):
        self.ensure_one()
        data = {
            "report_code": "general_ledger",
            "doc_model": self._name,
            "options": {
                **self._build_filters(),
                "date_from": self.date_from.isoformat(),
                "date_to": self.date_to.isoformat(),
            },
        }
        return self.env.ref("custom_accounting_reports.action_report_custom_financial").report_action(self, data=data)

    def action_export_xlsx(self):
        self.ensure_one()
        options = {
            **self._build_filters(),
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
        }
        filename = "General_Ledger_%s_%s.xlsx" % (self.date_from, self.date_to)
        report = self.env["custom.report.general.ledger"].with_context(
            gl_layout=self.gl_layout,
            gl_columns=self._selected_columns(),
        )
        return report._xlsx_action(options, filename)
