# -*- coding: utf-8 -*-
from odoo import fields, models


STEP_KINDS = [
    ("email", "Send Email"),
    ("wait", "Wait"),
    ("tag", "Tag Partner"),
]


class MarketingCampaignStep(models.Model):
    _name = "marketing.campaign.step"
    _description = "Marketing Campaign Step"
    _order = "campaign_id, sequence"

    campaign_id = fields.Many2one("marketing.campaign", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    kind = fields.Selection(STEP_KINDS, required=True, default="email")
    mail_template_id = fields.Many2one("mail.template")
    wait_hours = fields.Float(default=24.0)
    partner_category_id = fields.Many2one("res.partner.category")
