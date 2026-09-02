# -*- coding: utf-8 -*-
"""Wizard expanding a product / picking selection into a label print job.

The expansion is done in Python (not QWeb) so the ``ir.config_parameter`` cap
can be enforced *before* a print job is handed to wkhtmltopdf. The cap raises a
``UserError`` naming the limit — we never silently truncate a label run,
because a short sticker roll is worse than a refused print.
"""

from __future__ import annotations

import math

from odoo import _, api, fields, models
from odoo.exceptions import UserError

#: ``ir.config_parameter`` key + fallback for the maximum labels per print job.
MAX_LABELS_PARAM = "custom_wms_docs.max_labels"
MAX_LABELS_DEFAULT = 500


class WmsLabelWizard(models.TransientModel):
    _name = "custom.wms.label.wizard"
    _description = "WMS Price Tag / Product Label Wizard"

    picking_id = fields.Many2one(
        "stock.picking",
        string="Transfer",
        help="Optional. Required when the quantity source is the transfer.",
    )
    product_ids = fields.Many2many("product.product", string="Products")
    qty_source = fields.Selection(
        [
            ("manual", "Manual quantity"),
            ("picking_qty", "One label per unit shipped"),
        ],
        string="Quantity Source",
        default="manual",
        required=True,
    )
    qty_per_product = fields.Integer(string="Labels per Product", default=1)
    label_kind = fields.Selection(
        [
            ("price_tag", "Price Tag"),
            ("product_label", "Product Label"),
        ],
        string="Label Type",
        default="price_tag",
        required=True,
    )
    barcode_kind = fields.Selection(
        [
            ("Code128", "Code 128"),
            ("QR", "QR Code"),
            ("datamatrix", "Data Matrix"),
        ],
        string="Symbology",
        default="QR",
        required=True,
    )

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_model = self.env.context.get("active_model")
        active_ids = self.env.context.get("active_ids") or []
        if not active_ids:
            return res
        if active_model == "stock.picking" and "picking_id" in fields_list:
            res["picking_id"] = active_ids[0]
            res.setdefault("qty_source", "picking_qty")
        elif active_model == "product.product" and "product_ids" in fields_list:
            res["product_ids"] = [(6, 0, active_ids)]
        elif active_model == "product.template" and "product_ids" in fields_list:
            templates = self.env["product.template"].browse(active_ids).exists()
            res["product_ids"] = [(6, 0, templates.product_variant_ids.ids)]
        return res

    @api.onchange("picking_id")
    def _onchange_picking_id(self):
        for wizard in self:
            if wizard.picking_id and not wizard.product_ids:
                wizard.product_ids = wizard.picking_id.move_ids.product_id

    # ------------------------------------------------------------------
    # Cap
    # ------------------------------------------------------------------
    def _max_labels(self) -> int:
        """Configured maximum number of labels per print job."""
        raw = self.env["ir.config_parameter"].sudo().get_param(MAX_LABELS_PARAM, MAX_LABELS_DEFAULT)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = MAX_LABELS_DEFAULT
        return value if value > 0 else MAX_LABELS_DEFAULT

    # ------------------------------------------------------------------
    # Expansion
    # ------------------------------------------------------------------
    def _picking_label_source(self) -> list[tuple]:
        """Return ``[(product, qty), ...]`` for the picking.

        Detailed operations (``move_line_ids``) win when they exist — that is
        the done/picked quantity. A not-yet-reserved transfer falls back to the
        demanded quantity on ``move_ids``.
        """
        self.ensure_one()
        picking = self.picking_id
        pairs: list[tuple] = []
        lines = picking.move_line_ids.filtered(lambda ml: ml.product_id)
        if lines:
            for line in lines:
                qty = line.quantity or 0.0
                if "quantity_product_uom" in line._fields and line.quantity_product_uom:
                    qty = line.quantity_product_uom
                pairs.append((line.product_id, qty))
        else:
            for move in picking.move_ids.filtered(lambda m: m.product_id):
                pairs.append((move.product_id, move.product_uom_qty or 0.0))
        return pairs

    def _expand_labels(self) -> list[dict]:
        """Return one dict (``{"product_id": id}``) per physical sticker."""
        self.ensure_one()
        labels: list[dict] = []

        if self.qty_source == "picking_qty" and self.picking_id:
            selected = set(self.product_ids.ids)
            for product, qty in self._picking_label_source():
                if selected and product.id not in selected:
                    continue
                count = int(math.ceil(qty or 0.0))
                if count <= 0:
                    continue
                labels.extend([{"product_id": product.id}] * count)
        else:
            if not self.product_ids:
                raise UserError(_("Select at least one product to print labels for."))
            count = self.qty_per_product if self.qty_per_product > 0 else 1
            for product in self.product_ids:
                labels.extend([{"product_id": product.id}] * count)

        if not labels:
            raise UserError(_("Nothing to print: the selection produced zero labels."))

        cap = self._max_labels()
        if len(labels) > cap:
            raise UserError(
                _(
                    "This print job would produce %(count)s labels, which exceeds the "
                    "configured maximum of %(cap)s (system parameter '%(param)s'). "
                    "Narrow the selection or raise the limit.",
                    count=len(labels),
                    cap=cap,
                    param=MAX_LABELS_PARAM,
                )
            )
        return labels

    # ------------------------------------------------------------------
    # Print
    # ------------------------------------------------------------------
    def action_print(self):
        """Return the label report action carrying the expanded label list."""
        self.ensure_one()
        labels = self._expand_labels()
        docids = list(dict.fromkeys(label["product_id"] for label in labels))
        data = {
            "labels": labels,
            "label_kind": self.label_kind,
            "barcode_kind": self.barcode_kind,
            "picking_id": self.picking_id.id or False,
        }
        report = self.env.ref("custom_wms_docs.action_report_wms_product_label")
        return report.with_context(discard_logo_check=True).report_action(docids, data=data)
