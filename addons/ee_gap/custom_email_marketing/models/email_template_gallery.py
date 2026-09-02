# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class CustomEmailTemplateGallery(models.Model):
    _name = "custom.email.template.gallery"
    _description = "Email Template Gallery"
    _inherit = ["mail.thread"]
    _order = "category, name"

    name = fields.Char(required=True, tracking=True)
    category = fields.Selection(
        [
            ("welcome", "Welcome"),
            ("newsletter", "Newsletter"),
            ("promo", "Promotional"),
            ("transactional", "Transactional"),
            ("reminder", "Reminder"),
        ],
        default="newsletter",
        required=True,
        tracking=True,
    )
    language_code = fields.Char(
        string="Language",
        default="id",
        help="ISO language code, defaults to 'id' (Bahasa Indonesia).",
    )
    subject = fields.Char()
    body_html = fields.Html(sanitize=False)
    preview_thumbnail = fields.Binary(attachment=True)
    tag_ids = fields.Many2many(
        "mailing.list",
        string="Suggested Mailing Lists",
    )
    times_used = fields.Integer(default=0, readonly=True)
    active = fields.Boolean(default=True)

    # ------------------------------------------------------------------
    # Apply-to-mailing
    # ------------------------------------------------------------------

    def action_apply_to_mailing(self, mailing_id):
        """Clone this gallery template into the given mailing.

        Writes:
        - mailing.subject (only if non-empty here)
        - mailing.body_arch (HTML editor source)
        - mailing.body_html (rendered body)
        - mailing.x_gallery_template_id = self.id

        Also bumps ``times_used`` for usage telemetry.
        """
        self.ensure_one()
        if not mailing_id:
            raise UserError(_("No mailing supplied to apply this template to."))
        Mailing = self.env["mailing.mailing"]
        mailing = Mailing.browse(int(mailing_id))
        if not mailing.exists():
            raise UserError(_("Target mailing %s not found.") % mailing_id)

        vals = {
            "x_gallery_template_id": self.id,
            "body_arch": self.body_html or "",
            "body_html": self.body_html or "",
        }
        if self.subject:
            vals["subject"] = self.subject
        mailing.write(vals)
        self.sudo().write({"times_used": self.times_used + 1})
        mailing.message_post(body=_("Applied template gallery entry: %s") % self.display_name)
        return True
