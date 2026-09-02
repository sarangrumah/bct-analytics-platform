# -*- coding: utf-8 -*-
from __future__ import annotations

from odoo import fields, models


class BankImportLog(models.Model):
    _name = "custom.bank.import.log"
    _description = "Bank Import Log"
    _order = "imported_at desc, id desc"
    _inherit = ["mail.thread"]

    name = fields.Char(compute="_compute_name", store=True)
    template_id = fields.Many2one("custom.bank.import.template", required=True, ondelete="restrict")
    journal_id = fields.Many2one("account.journal", required=True, ondelete="restrict", domain=[("type", "=", "bank")])
    statement_id = fields.Many2one(
        "account.bank.statement", ondelete="set null", help="Created bank statement, if any."
    )
    filename = fields.Char()
    file_hash = fields.Char(index=True, help="sha256 of raw bytes, for dedup.")
    line_count = fields.Integer(default=0)
    error_count = fields.Integer(default=0)
    imported_at = fields.Datetime(default=fields.Datetime.now)
    imported_by_id = fields.Many2one("res.users", default=lambda s: s.env.user, readonly=True)
    state = fields.Selection(
        [("imported", "Imported"), ("failed", "Failed"), ("partial", "Partial")],
        default="imported",
        required=True,
        index=True,
    )
    error_message = fields.Text()
    raw_payload = fields.Text(help="Raw upstream payload (H2H) or summary of errors (CSV).")
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company, required=True)

    def _compute_name(self):
        for rec in self:
            rec.name = f"BIL/{rec.id or '?'}/{rec.filename or rec.template_id.code or 'import'}"
