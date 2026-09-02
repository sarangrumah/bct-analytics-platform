# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class CustomEmailApplyTemplateWizard(models.TransientModel):
    _name = "custom.email.apply.template.wizard"
    _description = "Apply Email Template Gallery to Mailing"

    mailing_id = fields.Many2one(
        "mailing.mailing",
        string="Mailing",
        required=True,
        ondelete="cascade",
    )
    template_id = fields.Many2one(
        "custom.email.template.gallery",
        string="Template",
        required=True,
        domain="[('active','=',True)]",
    )
    preview_subject = fields.Char(related="template_id.subject", readonly=True)
    preview_body = fields.Html(related="template_id.body_html", readonly=True)
    preview_thumbnail = fields.Binary(
        related="template_id.preview_thumbnail",
        readonly=True,
    )

    def action_apply(self):
        self.ensure_one()
        if not self.template_id:
            raise UserError(_("Please select a template."))
        self.template_id.action_apply_to_mailing(self.mailing_id.id)
        return {
            "type": "ir.actions.act_window",
            "res_model": "mailing.mailing",
            "res_id": self.mailing_id.id,
            "view_mode": "form",
            "target": "current",
        }
