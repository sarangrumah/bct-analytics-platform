# -*- coding: utf-8 -*-
"""Rendering context for the price-tag / product-label grid.

The label report is data-driven: the wizard passes an already expanded list of
labels (one dict per physical sticker) through ``report_action(..., data=...)``.
When the report is triggered straight from a ``product.product`` list (no
wizard, hence no ``data``) we degrade to one label per selected product.
"""

from __future__ import annotations

from odoo import models

from .wms_barcode import wms_barcode_url


class ReportWmsProductLabel(models.AbstractModel):
    _name = "report.custom_wms_docs.report_wms_product_label"
    _description = "WMS Price Tag / Product Label Renderer"

    def _label_vals(self, product, label_kind: str, barcode_kind: str) -> dict:
        """Return the flat dict a single sticker is rendered from."""
        value = product.barcode or product.default_code or product.display_name or ""
        return {
            "product": product,
            "name": product.display_name or product.name or "",
            "default_code": product.default_code or "",
            "barcode_value": value,
            "barcode_src": wms_barcode_url(
                value,
                barcode_kind,
                width=140 if barcode_kind == "QR" else 420,
                height=140 if barcode_kind == "QR" else 70,
            ),
            "list_price": product.list_price or 0.0,
            "uom_name": product.uom_id.display_name or "",
            "show_price": label_kind == "price_tag",
        }

    def _get_report_values(self, docids, data=None) -> dict:
        data = data or {}
        label_kind = data.get("label_kind") or "price_tag"
        barcode_kind = data.get("barcode_kind") or "QR"
        labels = data.get("labels") or []
        if not labels:
            labels = [{"product_id": pid} for pid in (docids or [])]

        Product = self.env["product.product"]
        cache = {}
        rows: list[dict] = []
        for label in labels:
            pid = label.get("product_id")
            if not pid:
                continue
            product = cache.get(pid)
            if product is None:
                product = Product.browse(pid).exists()
                cache[pid] = product
            if not product:
                continue
            rows.append(self._label_vals(product, label_kind, barcode_kind))

        company = self.env.company
        return {
            "doc_ids": docids,
            "doc_model": "product.product",
            "docs": Product.browse(docids or []),
            "labels": rows,
            "label_kind": label_kind,
            "barcode_kind": barcode_kind,
            "company": company,
            "currency": company.currency_id,
        }
