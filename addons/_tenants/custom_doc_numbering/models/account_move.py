# -*- coding: utf-8 -*-
from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    def _get_starting_sequence(self):
        """Start customer-invoice numbering at INV/<CODE>/YYYY/MM/000.

        Only applies to posted customer invoices (``out_invoice``) on a sale
        journal of a company that has a document code; everything else falls
        back to Odoo's default. The ``/YYYY/MM/`` shape makes Odoo deduce a
        monthly reset automatically.

        Note: this only sets the FIRST number of a fresh sequence chain. A
        journal that already has invoices in the current period keeps following
        its existing pattern until a new chain starts.
        """
        self.ensure_one()
        code = self.company_id.x_doc_code
        if code and self.move_type == "out_invoice" and self.journal_id.type == "sale":
            move_date = self.date or self.invoice_date or fields.Date.context_today(self)
            return "INV/%s/%04d/%02d/000" % (code, move_date.year, move_date.month)
        return super()._get_starting_sequence()
