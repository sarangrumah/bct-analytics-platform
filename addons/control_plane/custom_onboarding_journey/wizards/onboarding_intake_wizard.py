# -*- coding: utf-8 -*-
"""Intake wizard — creates partner + journey from a one-page form."""

from __future__ import annotations

import json

from odoo import _, fields, models


class OnboardingIntakeWizard(models.TransientModel):
    _name = "onboarding.intake.wizard"
    _description = "Onboarding Intake Wizard"

    partner_name = fields.Char(required=True)
    partner_email = fields.Char(required=True)
    vertical_target = fields.Char()
    modules_wishlist = fields.Text(help="Free-form, comma- or newline-separated.")
    business_process_narrative = fields.Text()
    company_logo = fields.Binary()
    company_logo_filename = fields.Char()
    npwp = fields.Char()
    bank_name = fields.Char()
    bank_account = fields.Char()

    def action_submit(self):
        self.ensure_one()
        Partner = self.env["res.partner"]
        partner = Partner.search([("email", "=", self.partner_email)], limit=1)
        if not partner:
            partner_vals = {"name": self.partner_name, "email": self.partner_email}
            if self.company_logo:
                partner_vals["image_1920"] = self.company_logo
            partner = Partner.create(partner_vals)

        profile = {
            "name": self.partner_name,
            "email": self.partner_email,
            "vertical_target": self.vertical_target,
            "modules_wishlist": self.modules_wishlist,
            "narrative": self.business_process_narrative,
            "npwp": self.npwp,
            "bank": {"name": self.bank_name, "account": self.bank_account},
        }

        journey = self.env["onboarding.journey"].create(
            {
                "name": _("Onboarding - %s") % self.partner_name,
                "partner_id": partner.id,
                "stage": "intake",
                "company_profile_json": json.dumps(profile, ensure_ascii=False),
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "onboarding.journey",
            "res_id": journey.id,
            "view_mode": "form",
        }
