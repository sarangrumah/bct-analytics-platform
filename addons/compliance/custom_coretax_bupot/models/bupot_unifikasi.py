# -*- coding: utf-8 -*-
"""Bukti Potong PPh Unifikasi — period header.

A header groups one calendar month of withholding slips for a single
company. The lifecycle is:

    draft   -> generated  (after XML export wizard run)
            -> submitted (after operator confirms upload to Coretax portal)
            -> accepted | rejected (after DJP response)

`pdp.audited.mixin` ensures every transition is hash-chained into the
append-only audit log.
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CustomBupotUnifikasi(models.Model):
    _name = "custom.bupot.unifikasi"
    _inherit = ["pdp.audited.mixin", "mail.thread", "mail.activity.mixin"]
    _description = "Bukti Potong PPh Unifikasi (Coretax) — Period"
    _order = "year desc, month desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one(
        comodel_name="res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    month = fields.Selection(
        selection=[(str(m), f"{m:02d}") for m in range(1, 13)],
        required=True,
    )
    year = fields.Char(required=True, size=4)
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("generated", "XML Generated"),
            ("submitted", "Submitted to DJP"),
            ("accepted", "Accepted"),
            ("rejected", "Rejected"),
        ],
        default="draft",
        tracking=True,
        required=True,
    )
    line_ids = fields.One2many(
        comodel_name="custom.bupot.unifikasi.line",
        inverse_name="bupot_id",
    )
    line_count = fields.Integer(compute="_compute_line_count")
    total_withheld = fields.Float(compute="_compute_totals")
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        default=lambda self: self.env.company.currency_id,
    )
    rejection_note = fields.Text()

    _period_unique = models.Constraint(
        "UNIQUE(company_id, month, year)",
        "Only one Bukti Potong period per company per month is allowed.",
    )

    @api.depends("month", "year", "company_id")
    def _compute_name(self):
        for rec in self:
            rec.name = f"BPU/{rec.year or '----'}/{rec.month or '--'}"

    @api.depends("line_ids")
    def _compute_line_count(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)

    @api.depends("line_ids.withheld_amount")
    def _compute_totals(self):
        for rec in self:
            rec.total_withheld = sum(rec.line_ids.mapped("withheld_amount"))

    # --------- workflow ---------

    def action_generate_xml(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_("Period %s has no bupot lines.") % self.name)
        return {
            "type": "ir.actions.act_window",
            "name": _("Export Bupot XML"),
            "res_model": "custom.bupot.xml.export.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_bupot_id": self.id},
        }

    def action_mark_submitted(self):
        for rec in self:
            if rec.state not in ("generated", "draft"):
                raise UserError(_("Only Draft/Generated periods can be marked submitted."))
            rec.state = "submitted"

    def action_mark_accepted(self):
        for rec in self:
            if rec.state != "submitted":
                raise UserError(_("Only Submitted periods can be Accepted."))
            rec.state = "accepted"

    def action_mark_rejected(self):
        for rec in self:
            rec.state = "rejected"

    def action_reset_draft(self):
        for rec in self:
            rec.state = "draft"

    def action_open_number_upload(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Upload DJP Numbers (CSV)"),
            "res_model": "custom.bupot.number.upload.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_bupot_id": self.id},
        }
