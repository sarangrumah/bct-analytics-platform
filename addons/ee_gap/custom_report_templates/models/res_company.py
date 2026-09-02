# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    report_bank_details = fields.Text(
        string="Report Bank / Tax Details",
        help="Bank account, NPWP and payment details printed in the "
        "OTHER COMMENTS box of customer invoices. Use line breaks to "
        "separate lines. Per-tenant — each company sets its own.",
    )
    report_show_product_name = fields.Boolean(
        string="Print Product Name Above Description",
        help="Print the product name as a bold first line of every document "
        "line, above its description. Turn this on for companies whose users "
        "overwrite the line description with free text (event details, "
        "specifications) and would otherwise lose the product name on the PDF. "
        "Off by default so existing tenants keep their current layout.",
    )
    report_footer_note = fields.Char(
        string="Report Footer Note",
        default="Thank You For Your Business",
        help="Short note centred at the bottom of every branded document.",
    )
