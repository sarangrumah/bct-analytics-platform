# -*- coding: utf-8 -*-
"""Aged Payable: open AP per vendor bucketed by overdue days."""

from odoo import models


class CustomReportAgedPayable(models.AbstractModel):
    _name = "custom.report.aged.payable"
    _inherit = "custom.report.aged.receivable"
    _description = "Custom Aged Payable"

    _report_code = "aged_payable"
    _report_title = "Aged Payable"

    _doc_no_header = "Bill Number"
    _reference_header = "Bill Reference"
    _account_header = "Payable Account"

    def _account_type(self):
        return "liability_payable"
