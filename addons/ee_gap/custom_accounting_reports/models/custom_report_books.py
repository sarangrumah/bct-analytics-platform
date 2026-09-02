# -*- coding: utf-8 -*-
"""Day Book / Cash Book / Bank Book / Journal Audit.

A shared :class:`CustomReportBookMixin` lists posted move-lines grouped
by date and journal. Subclasses pin a journal-type filter to specialise
the view.

The mixin itself is a plain AbstractModel; subclasses inherit it and
override ``_book_journal_filter``. None of these are SQL views, so
``_auto`` stays True (default for AbstractModel).
"""

from odoo import models


class CustomReportBookMixin(models.AbstractModel):
    """Shared logic for the daily-books family."""

    _name = "custom.report.book.mixin"
    _inherit = "custom.report.engine"
    _description = "Custom Book Mixin"

    # Override per subclass: 'cash', 'bank', or None for all.
    _book_journal_filter = None

    def _xlsx_columns(self):
        return [
            {"header": "Date", "field": "date", "kind": "date", "width": 12},
            {"header": "Journal", "field": "journal_code", "kind": "text", "width": 12},
            {"header": "Entry", "field": "move_name", "kind": "text", "width": 18},
            {"header": "Account", "field": "account_code", "kind": "text", "width": 14},
            {"header": "Partner", "field": "partner", "kind": "text", "width": 28},
            {"header": "Label", "field": "label", "kind": "text", "width": 34},
            {"header": "Debit", "field": "debit", "kind": "number", "width": 16},
            {"header": "Credit", "field": "credit", "kind": "number", "width": 16},
        ]

    def _get_journal_lines(self, filters):
        """Return raw move-lines respecting the book's journal scope."""
        Journal = self.env["account.journal"]
        domain = []
        if self._book_journal_filter:
            domain.append(("type", "=", self._book_journal_filter))
        if filters.get("journal_ids"):
            domain.append(("id", "in", filters["journal_ids"]))
        journals = Journal.search(domain) if domain else Journal.search([])
        AML = self.env["account.move.line"]
        line_domain = self._base_move_line_domain(filters) + [
            ("journal_id", "in", journals.ids),
        ]
        return AML.search(line_domain, order="date, move_id, id")

    def _build_lines(self, filters):
        move_lines = self._get_journal_lines(filters)
        lines = []
        total_debit = total_credit = 0.0
        for ml in move_lines:
            lines.append(
                {
                    "type": "entry",
                    "date": ml.date,
                    "journal_code": ml.journal_id.code,
                    "move_name": ml.move_id.name or ml.move_id.display_name,
                    "account_code": self._account_code(ml.account_id),
                    "partner": ml.partner_id.display_name or "",
                    "label": ml.name or "",
                    "debit": ml.debit,
                    "credit": ml.credit,
                }
            )
            total_debit += ml.debit
            total_credit += ml.credit
        lines.append(
            {
                "type": "grand_total",
                "label": "Grand Total",
                "debit": total_debit,
                "credit": total_credit,
            }
        )
        return lines


class CustomReportDayBook(models.AbstractModel):
    _name = "custom.report.day.book"
    _inherit = "custom.report.book.mixin"
    _description = "Custom Day Book"
    _report_code = "day_book"
    _report_title = "Day Book"
    _book_journal_filter = None


class CustomReportCashBook(models.AbstractModel):
    _name = "custom.report.cash.book"
    _inherit = "custom.report.book.mixin"
    _description = "Custom Cash Book"
    _report_code = "cash_book"
    _report_title = "Cash Book"
    _book_journal_filter = "cash"


class CustomReportBankBook(models.AbstractModel):
    _name = "custom.report.bank.book"
    _inherit = "custom.report.book.mixin"
    _description = "Custom Bank Book"
    _report_code = "bank_book"
    _report_title = "Bank Book"
    _book_journal_filter = "bank"


class CustomReportJournalAudit(models.AbstractModel):
    """Posted move audit: who posted what, when, from where."""

    _name = "custom.report.journal.audit"
    _inherit = "custom.report.engine"
    _description = "Custom Journal Audit"
    _report_code = "journal_audit"
    _report_title = "Journal Audit"

    def _xlsx_columns(self):
        return [
            {"header": "Date", "field": "date", "kind": "date", "width": 12},
            {"header": "Journal", "field": "journal_code", "kind": "text", "width": 12},
            {"header": "Entry", "field": "move_name", "kind": "text", "width": 18},
            {"header": "Reference", "field": "reference", "kind": "text", "width": 22},
            {"header": "State", "field": "state", "kind": "text", "width": 12},
            {"header": "Posted By", "field": "posted_by", "kind": "text", "width": 22},
            {"header": "Posted On", "field": "posted_on", "kind": "text", "width": 22},
            {"header": "Amount", "field": "amount_total", "kind": "number", "width": 18},
        ]

    def _build_lines(self, filters):
        AccountMove = self.env["account.move"]
        domain = [
            ("date", ">=", filters["date_from"]),
            ("date", "<=", filters["date_to"]),
            ("company_id", "in", filters["company_ids"]),
        ]
        if filters.get("posted_only", True):
            domain.append(("state", "=", "posted"))
        else:
            domain.append(("state", "in", ("draft", "posted")))
        if filters.get("journal_ids"):
            domain.append(("journal_id", "in", filters["journal_ids"]))

        moves = AccountMove.search(domain, order="date, journal_id, id")
        lines = []
        for move in moves:
            lines.append(
                {
                    "type": "move",
                    "date": move.date,
                    "journal_code": move.journal_id.code,
                    "move_name": move.name or move.display_name,
                    "reference": move.ref or "",
                    "state": move.state,
                    "posted_by": (move.create_uid.display_name if move.create_uid else ""),
                    "posted_on": (move.create_date.isoformat() if move.create_date else ""),
                    "amount_total": move.amount_total_signed,
                }
            )
        return lines
