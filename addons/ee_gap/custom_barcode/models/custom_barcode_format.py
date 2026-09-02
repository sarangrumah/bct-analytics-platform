# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CustomBarcodeFormat(models.Model):
    """Auto-generate barcodes on records of selected models using a configured
    ir.sequence + prefix/suffix. Optionally produces an EAN-13 check digit."""

    _name = "custom.barcode.format"
    _description = "Barcode Format"
    _order = "sequence, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    code = fields.Selection(
        [
            ("Code128", "CODE128"),
            ("EAN13", "EAN-13"),
            ("EAN8", "EAN-8"),
            ("QR", "QR"),
        ],
        default="Code128",
        required=True,
    )
    prefix = fields.Char(default="")
    suffix = fields.Char(default="")
    sequence_id = fields.Many2one(
        "ir.sequence",
        string="Number Sequence",
        help="Sequence used to draw the running number embedded in the barcode.",
    )
    applied_models = fields.Many2many(
        "ir.model",
        "custom_barcode_format_model_rel",
        "format_id",
        "model_id",
        domain=[
            (
                "model",
                "in",
                [
                    "product.product",
                    "product.template",
                    "stock.lot",
                    "stock.location",
                ],
            )
        ],
        string="Applied To",
    )
    company_id = fields.Many2one(
        "res.company",
        default=lambda s: s.env.company,
    )
    active = fields.Boolean(default=True)

    @api.model
    def _format_for_model(self, model_name):
        return self.search(
            [
                ("active", "=", True),
                ("applied_models.model", "=", model_name),
                ("company_id", "in", (False, self.env.company.id)),
            ],
            limit=1,
            order="sequence, id",
        )

    def generate(self):
        """Return a fresh barcode string for this format."""
        self.ensure_one()
        if not self.sequence_id:
            raise UserError(_("Configure a sequence on format %s.") % self.name)
        seq = self.sequence_id.next_by_id()
        raw = "%s%s%s" % (self.prefix or "", seq or "", self.suffix or "")
        if self.code == "EAN13":
            return self._ensure_ean13(raw)
        if self.code == "EAN8":
            return self._ensure_ean8(raw)
        return raw

    @staticmethod
    def _ensure_ean13(raw):
        digits = "".join(c for c in raw if c.isdigit())[:12].rjust(12, "0")
        odd = sum(int(d) for i, d in enumerate(digits) if i % 2 == 0)
        even = sum(int(d) for i, d in enumerate(digits) if i % 2 == 1)
        check = (10 - (odd + 3 * even) % 10) % 10
        return digits + str(check)

    @staticmethod
    def _ensure_ean8(raw):
        digits = "".join(c for c in raw if c.isdigit())[:7].rjust(7, "0")
        odd = sum(int(d) for i, d in enumerate(digits) if i % 2 == 0)
        even = sum(int(d) for i, d in enumerate(digits) if i % 2 == 1)
        check = (10 - (3 * odd + even) % 10) % 10
        return digits + str(check)


class _BarcodeAutoMixin(models.AbstractModel):
    """Shared helper: on create, if a barcode field is empty and a format is
    configured for this model, auto-populate it."""

    _name = "custom.barcode.auto.mixin"
    _description = "Auto-Barcode Helper"

    @api.model
    def _custom_barcode_autogenerate(self, vals_list, field="barcode"):
        Fmt = self.env["custom.barcode.format"].sudo()
        fmt = Fmt._format_for_model(self._name)
        if not fmt:
            return
        for vals in vals_list:
            if not vals.get(field):
                try:
                    vals[field] = fmt.generate()
                except Exception as e:  # pragma: no cover
                    _logger.warning("Auto-barcode failed for %s: %s", self._name, e)


class ProductProduct(models.Model):
    _inherit = "product.product"

    @api.model_create_multi
    def create(self, vals_list):
        self.env["custom.barcode.auto.mixin"]._custom_barcode_autogenerate(vals_list, field="barcode")
        return super().create(vals_list)


class StockLot(models.Model):
    _inherit = "stock.lot"

    @api.model_create_multi
    def create(self, vals_list):
        self.env["custom.barcode.auto.mixin"]._custom_barcode_autogenerate(vals_list, field="name")
        return super().create(vals_list)
