# -*- coding: utf-8 -*-
import base64
import hashlib

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CustomQualitySignature(models.Model):
    """Internal e-signature attached to a quality.check (or to a CAPA).

    Hash is computed at create time over signer + context + image bytes;
    subsequent edits to the protected fields are blocked, surfaced via the
    `is_valid` computed field as a tamper indicator."""

    _name = "custom.quality.signature"
    _description = "Quality Inspection Signature"
    _order = "signed_at desc, id desc"

    check_id = fields.Many2one(
        "quality.check",
        ondelete="cascade",
        index=True,
    )
    capa_id = fields.Many2one(
        "custom.quality.capa",
        ondelete="cascade",
        index=True,
    )
    signer_id = fields.Many2one(
        "res.users",
        default=lambda s: s.env.user,
    )
    signer_name = fields.Char()
    signer_email = fields.Char()
    signed_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    purpose = fields.Selection(
        [
            ("operator", "Operator"),
            ("supervisor", "Supervisor"),
            ("customer", "Customer"),
        ],
        default="operator",
        required=True,
    )
    image = fields.Binary(string="Signature Image", attachment=True)
    image_filename = fields.Char(default="signature.png")
    hash = fields.Char(readonly=True, copy=False)
    is_valid = fields.Boolean(compute="_compute_is_valid")

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if not rec.signer_name and rec.signer_id:
                rec.signer_name = rec.signer_id.name
            if not rec.signer_email and rec.signer_id:
                rec.signer_email = rec.signer_id.login
            rec.hash = rec._compute_hash()
        return records

    def _compute_hash(self):
        self.ensure_one()
        image_bytes = b""
        if isinstance(self.image, (bytes, bytearray)):
            image_bytes = bytes(self.image)
        elif self.image:
            try:
                image_bytes = base64.b64decode(self.image, validate=False)
            except Exception:
                image_bytes = b""
        payload = "|".join(
            [
                str(self.signer_id.id or ""),
                str(self.check_id.id or ""),
                str(self.capa_id.id or ""),
                (self.signed_at or fields.Datetime.now()).isoformat() if self.signed_at else "",
                hashlib.sha256(image_bytes).hexdigest(),
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @api.depends("signer_id", "signed_at", "image", "check_id", "capa_id")
    def _compute_is_valid(self):
        for r in self:
            r.is_valid = bool(r.hash) and (r.hash == r._compute_hash())

    def write(self, vals):
        protected = {"signer_id", "signed_at", "check_id", "capa_id", "image", "image_filename", "purpose"}
        if any(r.hash for r in self) and (set(vals) & protected):
            raise ValidationError(_("Cannot modify a signed signature. Create a new one instead."))
        return super().write(vals)
