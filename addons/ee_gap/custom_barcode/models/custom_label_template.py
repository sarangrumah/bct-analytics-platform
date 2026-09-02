# -*- coding: utf-8 -*-
import logging
import re

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_FIELD_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_\.]*)\s*\}\}")


class CustomLabelTemplate(models.Model):
    """Renderable label template (ZPL / ESC/POS / PDF placeholder).

    The template_source uses ``{{field}}`` or ``{{rel.field}}`` placeholders
    that are substituted against an arbitrary record at render time.
    """

    _name = "custom.label.template"
    _description = "Label Template"
    _order = "sequence, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    paper_format = fields.Selection(
        [
            ("zebra_2x1", "Zebra 2x1 inch (203 dpi)"),
            ("zebra_4x6", "Zebra 4x6 inch (203 dpi)"),
            ("thermal_50x30", "Thermal 50x30 mm"),
            ("a4_30up", "A4 30-up sheet"),
            ("custom", "Custom (use width_mm / height_mm)"),
        ],
        default="zebra_2x1",
        required=True,
    )
    width_mm = fields.Float(default=50.0)
    height_mm = fields.Float(default=25.0)
    output_mode = fields.Selection(
        [
            ("zpl", "ZPL (Zebra)"),
            ("escpos", "ESC/POS (Thermal)"),
            ("pdf", "PDF"),
        ],
        default="zpl",
        required=True,
    )
    template_source = fields.Text(
        help="Template body. Use {{field}} placeholders (dot-notation for "
        "related fields, e.g. {{product_id.default_code}}).",
    )
    applies_to = fields.Many2one(
        "ir.model",
        string="Applies To",
        help="Target model whose records this template renders.",
    )
    company_id = fields.Many2one(
        "res.company",
        default=lambda s: s.env.company,
    )
    active = fields.Boolean(default=True)
    notes = fields.Text()

    def _resolve(self, record, path):
        """Walk dotted path on the record."""
        value = record
        for part in path.split("."):
            if value is False or value is None:
                return ""
            value = getattr(value, part, "")
            if hasattr(value, "_name") and len(value) > 1:
                value = value[:1]
        if hasattr(value, "_name"):
            value = getattr(value, "display_name", "") or ""
        return "" if value is False else str(value)

    def render(self, record, qty=1):
        """Return rendered bytes (the chosen encoding)."""
        self.ensure_one()
        if not self.template_source:
            raise UserError(_('Template "%s" has empty body.') % self.name)
        body = self.template_source

        def repl(match):
            path = match.group(1)
            return self._resolve(record, path)

        rendered = _FIELD_RE.sub(repl, body)
        if qty > 1:
            rendered = "\n".join([rendered] * qty)
        return rendered.encode("utf-8")

    def get_paper_dim_mm(self):
        self.ensure_one()
        if self.paper_format == "custom":
            return (self.width_mm, self.height_mm)
        return {
            "zebra_2x1": (50.8, 25.4),
            "zebra_4x6": (101.6, 152.4),
            "thermal_50x30": (50.0, 30.0),
            "a4_30up": (210.0, 297.0),
        }.get(self.paper_format, (50.8, 25.4))
