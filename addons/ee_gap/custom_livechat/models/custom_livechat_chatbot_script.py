# -*- coding: utf-8 -*-
from odoo import api, fields, models


class CustomLivechatChatbotScript(models.Model):
    _name = "custom.livechat.chatbot.script"
    _description = "Live Chat Chatbot Script"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    channel_id = fields.Many2one(
        "im_livechat.channel",
        string="Live Chat Channel",
        ondelete="set null",
        tracking=True,
    )
    is_active = fields.Boolean(string="Active", default=True, tracking=True)
    step_ids = fields.One2many(
        "custom.livechat.chatbot.step",
        "script_id",
        string="Steps",
        copy=True,
    )
    step_count = fields.Integer(string="Step Count", compute="_compute_step_count")

    @api.depends("step_ids")
    def _compute_step_count(self):
        for rec in self:
            rec.step_count = len(rec.step_ids)

    def get_first_step(self):
        """Return the first step (lowest sequence) of this script, if any."""
        self.ensure_one()
        steps = self.step_ids.sorted(key=lambda s: (s.sequence, s.id))
        return steps[:1]
