# -*- coding: utf-8 -*-
"""Recompute forum.post.x_helpful_count when votes change."""

from odoo import api, models


class ForumPostVote(models.Model):
    _inherit = "forum.post.vote"

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs.post_id._compute_x_helpful_count()
        return recs

    def write(self, vals):
        res = super().write(vals)
        if "vote" in vals:
            self.post_id._compute_x_helpful_count()
        return res

    def unlink(self):
        posts = self.mapped("post_id")
        res = super().unlink()
        posts._compute_x_helpful_count()
        return res
