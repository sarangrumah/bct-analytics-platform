# -*- coding: utf-8 -*-
"""Day Book wizard — also serves Cash / Bank Book / Journal Audit via
the ``book_type`` selector. Single wizard model keeps the UX consistent
across the four daily-books reports.
"""

from datetime import date

from odoo import fields, models


class DayBookWizard(models.TransientModel):
    _name = "custom.report.day.book.wizard"
    _inherit = "custom.report.wizard.mixin"
    _description = "Day / Cash / Bank Book / Journal Audit Wizard"

    def _report_code_for_view(self):
        # One wizard serves four reports, selected by ``book_type``.
        return self.book_type

    book_type = fields.Selection(
        selection=[
            ("day_book", "Day Book"),
            ("cash_book", "Cash Book"),
            ("bank_book", "Bank Book"),
            ("journal_audit", "Journal Audit"),
        ],
        default="day_book",
        required=True,
    )
    date_from = fields.Date(
        required=True,
        default=lambda self: date.today().replace(day=1),
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
    posted_only = fields.Boolean(default=True)

    def _build_filters(self):
        self.ensure_one()
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "journal_ids": self.journal_ids.ids,
            "posted_only": self.posted_only,
        }

    def action_print(self):
        self.ensure_one()
        data = {
            "report_code": self.book_type,
            "doc_model": self._name,
            "options": {
                **self._build_filters(),
                "date_from": self.date_from.isoformat(),
                "date_to": self.date_to.isoformat(),
            },
        }
        return self.env.ref("custom_accounting_reports.action_report_custom_financial").report_action(self, data=data)

    _BOOK_MODEL = {
        "day_book": "custom.report.day.book",
        "cash_book": "custom.report.cash.book",
        "bank_book": "custom.report.bank.book",
        "journal_audit": "custom.report.journal.audit",
    }

    def action_export_xlsx(self):
        self.ensure_one()
        options = {
            **self._build_filters(),
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
        }
        model = self._BOOK_MODEL[self.book_type]
        filename = "%s_%s_%s.xlsx" % (
            self.book_type.title().replace("_", ""),
            self.date_from,
            self.date_to,
        )
        return self.env[model]._xlsx_action(options, filename)
