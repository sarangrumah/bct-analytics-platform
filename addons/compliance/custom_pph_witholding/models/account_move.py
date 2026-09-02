# -*- coding: utf-8 -*-
"""Expose the 'Apply Witholding' button on account.move."""

from __future__ import annotations

from odoo import _, models


class AccountMove(models.Model):
    _inherit = "account.move"

    def action_open_witholding_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Apply Witholding"),
            "res_model": "custom.apply.witholding.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_partner_id": self.partner_id.id,
                "default_amount": self.amount_untaxed or self.amount_total,
                "default_source_move_id": self.id,
            },
        }
