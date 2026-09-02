# -*- coding: utf-8 -*-
from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


def _get_param(env, key, default):
    return env["ir.config_parameter"].sudo().get_param("custom_affiliate.%s" % key, default)


class CustomAffiliate(models.Model):
    _name = "custom.affiliate"  # nosemgrep
    _description = "Affiliate"
    _inherit = ["mail.thread"]
    _order = "create_date desc"

    name = fields.Char(string="Name", related="partner_id.name", store=True, readonly=True)
    partner_id = fields.Many2one("res.partner", string="Partner", required=True, ondelete="restrict", tracking=True)
    affiliate_code = fields.Char(
        string="Affiliate Code",
        required=True,
        copy=False,
        index=True,
        tracking=True,
        default=lambda self: self._default_code(),
    )
    state = fields.Selection(
        [("draft", "Draft"), ("active", "Active"), ("suspended", "Suspended")],
        default="draft",
        required=True,
        tracking=True,
    )
    commission_rate = fields.Float(
        string="Commission Rate (%)",
        default=lambda self: float(_get_param(self.env, "default_commission_rate", "10")),
        tracking=True,
    )
    payout_method = fields.Selection(
        [("bank", "Bank Transfer"), ("ewallet", "E-Wallet"), ("manual", "Manual")],
        default="manual",
    )
    payout_details = fields.Char(string="Payout Account / Note")

    link_ids = fields.One2many("custom.affiliate.link", "affiliate_id", string="Links")
    click_count = fields.Integer(compute="_compute_counts")
    conversion_count = fields.Integer(compute="_compute_counts")
    total_commission = fields.Monetary(
        compute="_compute_counts",
        currency_field="currency_id",
        help="Approved + paid commission to date.",
    )
    currency_id = fields.Many2one("res.currency", default=lambda self: self.env.company.currency_id)

    _affiliate_code_uniq = models.Constraint(
        "unique(affiliate_code)",
        "The affiliate code must be unique.",
    )

    @api.model
    def _default_code(self):
        return self.env["ir.sequence"].next_by_code("custom.affiliate.code") or False

    def _compute_counts(self):
        Click = self.env["custom.affiliate.click"]
        Conv = self.env["custom.affiliate.conversion"]
        for aff in self:
            aff.click_count = Click.search_count([("affiliate_id", "=", aff.id)])
            convs = Conv.search([("affiliate_id", "=", aff.id)])
            aff.conversion_count = len(convs)
            aff.total_commission = sum(
                convs.filtered(lambda c: c.state in ("approved", "paid")).mapped("commission_amount")
            )

    @api.constrains("commission_rate")
    def _check_rate(self):
        for aff in self:
            if aff.commission_rate < 0 or aff.commission_rate > 100:
                raise ValidationError(_("Commission rate must be between 0 and 100."))

    def action_activate(self):
        self.write({"state": "active"})

    def action_suspend(self):
        self.write({"state": "suspended"})

    def _storefront_dashboard(self) -> dict:
        """Self-serve affiliate dashboard payload for the storefront."""
        self.ensure_one()
        Conv = self.env["custom.affiliate.conversion"].sudo()
        convs = Conv.search([("affiliate_id", "=", self.id)])
        by_state = {"pending": 0, "approved": 0, "reversed": 0, "paid": 0}
        for c in convs:
            by_state[c.state] = by_state.get(c.state, 0) + 1
        earned = sum(convs.filtered(lambda c: c.state in ("approved", "paid")).mapped("commission_amount"))
        pending_amt = sum(convs.filtered(lambda c: c.state == "pending").mapped("commission_amount"))
        return {
            "is_affiliate": True,
            "code": self.affiliate_code,
            "state": self.state,
            "commission_rate": self.commission_rate,
            "currency": self.currency_id.name or "IDR",
            "stats": {
                "clicks": self.click_count,
                "conversions": by_state,
                "earned": earned,
                "pending": pending_amt,
            },
            "links": [l._storefront_serialize() for l in self.link_ids],
        }

    @api.model
    def _resolve_active(self, code):
        """Return the active affiliate for ``code`` (case-insensitive), else empty."""
        if not code:
            return self.browse()
        return self.sudo().search([("affiliate_code", "=ilike", code.strip()), ("state", "=", "active")], limit=1)
