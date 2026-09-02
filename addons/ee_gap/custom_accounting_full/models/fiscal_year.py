# -*- coding: utf-8 -*-
"""Fiscal year period management with lock-date enforcement."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class FiscalYear(models.Model):
    _name = "custom.fiscal.year"
    _description = "Fiscal Year"
    _inherit = ["pdp.audited.mixin", "mail.thread"]
    _order = "date_from desc, id"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char()
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("open", "Open"),
            ("closed", "Closed"),
        ],
        default="draft",
        copy=False,
        tracking=True,
    )
    move_count = fields.Integer(
        compute="_compute_move_count",
        string="# Posted Moves",
    )

    @api.depends("date_from", "date_to", "company_id")
    def _compute_move_count(self):
        Move = self.env["account.move"]
        for fy in self:
            fy.move_count = Move.search_count(
                [
                    ("company_id", "=", fy.company_id.id),
                    ("date", ">=", fy.date_from),
                    ("date", "<=", fy.date_to),
                    ("state", "=", "posted"),
                ]
            )

    @api.constrains("date_from", "date_to", "company_id")
    def _check_dates_and_overlap(self):
        for fy in self:
            if fy.date_from > fy.date_to:
                raise ValidationError(
                    _(
                        "Fiscal year '%(name)s': start date must be before end date.",
                        name=fy.name,
                    )
                )
            overlap = self.search(
                [
                    ("id", "!=", fy.id),
                    ("company_id", "=", fy.company_id.id),
                    ("date_from", "<=", fy.date_to),
                    ("date_to", ">=", fy.date_from),
                ],
                limit=1,
            )
            if overlap:
                raise ValidationError(
                    _(
                        "Fiscal year '%(name)s' overlaps with '%(other)s' (%(d1)s — %(d2)s).",
                        name=fy.name,
                        other=overlap.name,
                        d1=overlap.date_from,
                        d2=overlap.date_to,
                    )
                )

    def action_open(self):
        self.write({"state": "open"})

    def action_reset_draft(self):
        for fy in self:
            if fy.state == "closed":
                raise ValidationError(_("Cannot reset a closed fiscal year to draft."))
            fy.state = "draft"

    def action_open_close_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Close Fiscal Year"),
            "res_model": "custom.fiscal.year.close.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_fiscal_year_id": self.id},
        }
