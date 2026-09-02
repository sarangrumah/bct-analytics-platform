# -*- coding: utf-8 -*-
"""Manual 'Apply Witholding' wizard, invoked from account.move."""

from __future__ import annotations

from odoo import _, fields, models
from odoo.exceptions import UserError


class CustomApplyWitholdingWizard(models.TransientModel):
    _name = "custom.apply.witholding.wizard"
    _description = "Apply Witholding Wizard"

    partner_id = fields.Many2one("res.partner", required=True)
    amount = fields.Float(digits=(16, 2), required=True)
    pph_type = fields.Selection(
        selection=[
            ("23", "PPh Pasal 23"),
            ("22", "PPh Pasal 22"),
            ("4_2", "PPh Pasal 4 ayat (2)"),
            ("15", "PPh Pasal 15"),
            ("26", "PPh Pasal 26"),
        ],
        default="23",
        required=True,
    )
    service_category = fields.Char(default="general")
    date = fields.Date(default=fields.Date.context_today, required=True)
    source_move_id = fields.Many2one("account.move")

    # Preview fields
    preview_rate = fields.Float(readonly=True, digits=(6, 4))
    preview_withheld = fields.Float(readonly=True, digits=(16, 2))
    preview_remain = fields.Float(readonly=True, digits=(16, 2))
    preview_has_npwp = fields.Boolean(readonly=True)
    preview_rule_id = fields.Many2one("custom.witholding.rate", readonly=True)

    application_id = fields.Many2one(
        "custom.witholding.application",
        readonly=True,
    )

    def action_preview(self):
        self.ensure_one()
        result = self.env["custom.witholding.engine"].compute(
            partner=self.partner_id,
            amount=self.amount,
            pph_type=self.pph_type,
            date=self.date,
            service_category=self.service_category,
        )
        self.preview_rate = result["rate"]
        self.preview_withheld = result["withheld"]
        self.preview_remain = result["gross_remain"]
        self.preview_has_npwp = result["has_npwp"]
        self.preview_rule_id = result["applicable_rule_id"] or False
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_apply(self):
        self.ensure_one()
        Engine = self.env["custom.witholding.engine"]
        result = Engine.compute_and_log(
            partner=self.partner_id,
            amount=self.amount,
            pph_type=self.pph_type,
            date=self.date,
            service_category=self.service_category,
            source_doc=self.source_move_id or None,
            state="applied",
        )
        if not result["applicable_rule_id"]:
            raise UserError(
                _("No active witholding rate matched for PPh %(t)s / %(c)s on %(d)s.")
                % {"t": self.pph_type, "c": self.service_category, "d": self.date}
            )
        self.application_id = result["application_id"]
        return {
            "type": "ir.actions.act_window",
            "res_model": "custom.witholding.application",
            "res_id": result["application_id"],
            "view_mode": "form",
            "target": "current",
        }
