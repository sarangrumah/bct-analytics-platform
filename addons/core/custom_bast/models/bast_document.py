# -*- coding: utf-8 -*-
from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class CustomBastDocument(models.Model):
    _name = "custom.bast.document"
    _description = "Berita Acara Serah Terima (Handover Document)"
    _inherit = ["pdp.audited.mixin", "mail.thread", "mail.activity.mixin"]
    _order = "date_handover desc, id desc"
    _rec_name = "name"

    name = fields.Char(
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: self.env._("New"),
        tracking=True,
    )
    reference = fields.Reference(
        selection="_selection_reference_models",
        string="Source Document",
        index=True,
        tracking=True,
    )
    kind = fields.Selection(
        [
            ("pickup", "Pickup"),
            ("return", "Return"),
            ("delivery", "Delivery"),
            ("installation", "Installation"),
            ("handover", "Handover"),
        ],
        required=True,
        default="handover",
        tracking=True,
    )
    date_handover = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        tracking=True,
    )
    location_id = fields.Many2one("stock.location", string="Location")
    location_text = fields.Char(string="Location (free text)")
    party_from_id = fields.Many2one("res.partner", string="From", required=True, tracking=True)
    party_to_id = fields.Many2one("res.partner", string="To", required=True, tracking=True)
    party_from_signature = fields.Binary(string="From Signature", attachment=True)
    party_from_signed_at = fields.Datetime(readonly=True)
    party_from_signed_by = fields.Char()
    party_to_signature = fields.Binary(string="To Signature", attachment=True)
    party_to_signed_at = fields.Datetime(readonly=True)
    party_to_signed_by = fields.Char()
    witness_id = fields.Many2one("res.users", string="Witness")
    gps_latitude = fields.Float(string="Latitude", digits=(10, 7))
    gps_longitude = fields.Float(string="Longitude", digits=(10, 7))
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("signed_one_side", "Signed (one side)"),
            ("completed", "Completed"),
            ("voided", "Voided"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
        copy=False,
    )
    note = fields.Text()
    line_ids = fields.One2many("custom.bast.line", "bast_id", string="Lines", copy=True)
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        required=True,
    )

    _name_uniq = models.Constraint(
        "unique(name)",
        "BAST number must be unique.",
    )

    @api.model
    def _selection_reference_models(self) -> list[tuple[str, str]]:
        models_list = self._get_referenceable_models()
        Model = self.env["ir.model"]
        result = []
        for tech, label in models_list:
            if tech in self.env:
                rec = Model.sudo().search([("model", "=", tech)], limit=1)
                result.append((tech, rec.name if rec else label))
        return result

    @api.model
    def _get_referenceable_models(self) -> list[tuple[str, str]]:
        # Subclasses / inheriting modules can extend this list.
        candidates = [
            ("stock.picking", "Transfer"),
            ("sale.order", "Sales Order"),
            ("purchase.order", "Purchase Order"),
            ("fsm.work.order", "Field Service"),
            ("rental.order", "Rental Order"),
        ]
        return [(t, lbl) for t, lbl in candidates if t in self.env]

    @api.constrains("party_from_id", "party_to_id")
    def _check_parties_distinct(self):
        for rec in self:
            if rec.party_from_id and rec.party_to_id and rec.party_from_id == rec.party_to_id:
                raise ValidationError(_("From and To parties must differ."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("custom.bast.document") or _("New")
        return super().create(vals_list)

    def _recompute_state(self):
        for rec in self:
            if rec.state == "voided":
                continue
            has_from = bool(rec.party_from_signature)
            has_to = bool(rec.party_to_signature)
            if has_from and has_to:
                rec.state = "completed"
            elif has_from or has_to:
                rec.state = "signed_one_side"
            else:
                rec.state = "draft"

    def action_sign_from(self, signature, signed_by=None, gps=None):
        self.ensure_one()
        if self.state in ("completed", "voided"):
            raise UserError(_("Cannot sign a %s BAST.") % self.state)
        vals = {
            "party_from_signature": signature,
            "party_from_signed_at": fields.Datetime.now(),
            "party_from_signed_by": signed_by or self.env.user.name,
        }
        if gps:
            vals["gps_latitude"], vals["gps_longitude"] = gps
        self.write(vals)
        self._recompute_state()

    def action_sign_to(self, signature, signed_by=None, gps=None):
        self.ensure_one()
        if self.state in ("completed", "voided"):
            raise UserError(_("Cannot sign a %s BAST.") % self.state)
        vals = {
            "party_to_signature": signature,
            "party_to_signed_at": fields.Datetime.now(),
            "party_to_signed_by": signed_by or self.env.user.name,
        }
        if gps:
            vals["gps_latitude"], vals["gps_longitude"] = gps
        self.write(vals)
        self._recompute_state()

    def action_void(self, reason: str | None = None):
        for rec in self:
            if rec.state == "completed" and not self.env.user.has_group("custom_bast.group_bast_manager"):
                raise UserError(_("Only BAST managers can void a completed BAST."))
            rec.state = "voided"
            if reason:
                rec.message_post(body=_("Voided: %s") % reason)

    def action_open_reference(self):
        self.ensure_one()
        if not self.reference:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": self.reference._name,
            "res_id": self.reference.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_sign_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "custom.bast.sign.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_bast_id": self.id},
        }
