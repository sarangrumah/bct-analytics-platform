# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class CustomLivechatCannedResponse(models.Model):
    _name = "custom.livechat.canned.response"
    _description = "Live Chat Canned Response"
    _inherit = ["mail.thread"]
    _order = "category, shortcut"

    name = fields.Char(required=True, tracking=True)
    shortcut = fields.Char(
        required=True,
        help="Type :shortcut to expand",
        tracking=True,
    )
    body = fields.Html(string="Body")
    category = fields.Char(string="Category", tracking=True)
    language = fields.Char(string="Language", default="id", tracking=True)
    times_used = fields.Integer(string="Times Used", default=0, readonly=True)
    active = fields.Boolean(default=True)

    _shortcut_unique = models.Constraint(
        "unique(shortcut)",
        "Each canned response shortcut must be unique.",
    )

    @api.constrains("shortcut")
    def _check_shortcut(self):
        for rec in self:
            if rec.shortcut and (" " in rec.shortcut or len(rec.shortcut) < 2):
                raise ValidationError("Shortcut must be at least 2 characters and contain no spaces.")

    def action_increment_usage(self):
        for rec in self:
            rec.times_used = (rec.times_used or 0) + 1
        return True

    @api.model
    def expand_canned(self, shortcut):
        """Expand a `:shortcut` token into its HTML body.

        Returns a dict ``{shortcut, body, name, found}``. Increments
        ``times_used`` when a match is found. Intended to be called from
        the discuss composer JS asset when the user types ``:shortcut``.
        """
        if not shortcut:
            return {"shortcut": shortcut or "", "body": "", "name": "", "found": False}
        normalized = shortcut.lstrip(":").strip()
        if not normalized:
            return {"shortcut": shortcut, "body": "", "name": "", "found": False}
        rec = self.search(
            [("shortcut", "=", normalized), ("active", "=", True)],
            limit=1,
        )
        if not rec:
            return {"shortcut": normalized, "body": "", "name": "", "found": False}
        rec.sudo().write({"times_used": (rec.times_used or 0) + 1})
        return {
            "shortcut": rec.shortcut,
            "body": rec.body or "",
            "name": rec.name or "",
            "found": True,
        }
