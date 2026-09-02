# -*- coding: utf-8 -*-
"""Mapping res.partner (Odoo mitra) <-> MSG019T.ID (Oracle memberId)."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class OracleMemberMap(models.Model):
    _name = "custom.ppob.oracle.member.map"
    _description = "Oracle Member Mapping (res.partner <-> MSG019T.ID)"
    _order = "partner_id"
    _inherit = ["mail.thread"]

    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Mitra",
        required=True,
        ondelete="restrict",
        domain=[("x_custom_ppob_is_mitra", "=", True)],
        tracking=True,
    )
    oracle_member_id = fields.Integer(
        string="Oracle Member ID",
        required=True,
        tracking=True,
        help="Value of MSG019T.ID for this mitra.",
    )
    oracle_member_no = fields.Char(string="Oracle Member No", readonly=True)
    oracle_member_group = fields.Char(string="Oracle Member Group", readonly=True)
    last_known_balance = fields.Monetary(
        currency_field="currency_id",
        readonly=True,
        help="Last DEPOSIT_BALANCE seen during balance mirror sync.",
    )
    last_balance_sync = fields.Datetime(readonly=True)
    active = fields.Boolean(default=True, tracking=True)
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )

    _partner_uniq = models.Constraint(
        "unique(partner_id)",
        "Each mitra can only be mapped to one Oracle member.",
    )

    _oracle_member_id_uniq = models.Constraint(
        "unique(oracle_member_id)",
        "Each Oracle member ID can only be mapped to one mitra.",
    )

    @api.constrains("oracle_member_id")
    def _check_oracle_member_id_positive(self):
        for rec in self:
            if rec.oracle_member_id <= 0:
                raise ValidationError(_("Oracle Member ID must be a positive integer."))

    def action_lookup_oracle(self):
        """Fetch MEMBER_NO, MEMBER_GROUP, DEPOSIT_BALANCE from Oracle."""
        self.ensure_one()
        conn = self.env["custom.ppob.oracle.connection"]._get_active()
        rows = conn.query(
            "SELECT id, member_no, member_group, deposit_balance FROM msg019t WHERE id = :id",
            {"id": self.oracle_member_id},
            fetch="one",
        )
        if not rows:
            raise UserError(_("Member ID %s tidak ditemukan di Oracle MSG019T.") % self.oracle_member_id)
        _id, member_no, member_group, balance = rows
        self.write(
            {
                "oracle_member_no": member_no,
                "oracle_member_group": member_group,
                "last_known_balance": balance or 0.0,
                "last_balance_sync": fields.Datetime.now(),
            }
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Oracle Lookup"),
                "message": _("Member %s (%s) — Balance: %s") % (member_no, member_group, balance),
                "type": "success",
            },
        }

    def action_view_partner(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Mitra"),
            "res_model": "res.partner",
            "res_id": self.partner_id.id,
            "view_mode": "form",
        }
