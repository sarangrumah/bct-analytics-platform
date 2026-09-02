# -*- coding: utf-8 -*-
from odoo import fields, models


class ImLivechatChannel(models.Model):
    _inherit = "im_livechat.channel"

    x_skill_tags = fields.Char(
        string="Skill Tags",
        help="Comma-separated skill tags used by the routing layer to match "
        "operators against visitor query keywords (e.g. 'billing,refund').",
    )

    def _skill_tag_list(self):
        self.ensure_one()
        if not self.x_skill_tags:
            return []
        return [t.strip().lower() for t in self.x_skill_tags.split(",") if t.strip()]
