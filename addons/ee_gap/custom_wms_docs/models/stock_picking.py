# -*- coding: utf-8 -*-
"""Data helpers backing the warehouse documents.

Everything the QWeb templates need is computed here so the templates stay
thin (and so the walk-path / weight arithmetic is unit-testable and later
replaceable without touching XML).

Odoo 19 notes baked into this file:

* ``stock.quant.package`` was renamed to ``stock.package``; the package type
  model is ``stock.package.type`` with ``packaging_length`` / ``width`` /
  ``height`` / ``base_weight``.
* ``stock.location`` has no ``posx`` / ``posy`` / ``posz`` any more, so the
  walk path is derived from ``complete_name`` only.
* ``expiration_date`` only exists when ``product_expiry`` is installed and
  ``carrier_id`` only when ``delivery`` is installed — both are probed through
  ``_fields`` so the reports never crash on a lean install.
"""

from __future__ import annotations

from odoo import models

from .wms_barcode import wms_barcode_url

#: Sort sentinel pushing products without an internal reference to the end of
#: the walk path (a bare "" would sort them first).
_NO_CODE = "￿"


class StockPicking(models.Model):
    _name = "stock.picking"
    # ``wms.barcode.mixin`` supplies ``_wms_barcode_url`` / ``_wms_barcode_src``
    # / ``_wms_barcode_png`` to both this model and the QWeb templates.
    _inherit = ["stock.picking", "wms.barcode.mixin"]

    # ------------------------------------------------------------------
    # Small field-probing helpers (optional modules)
    # ------------------------------------------------------------------
    def _wms_line_expiry(self, line):
        """Return the lot expiration date, or ``False`` without product_expiry."""
        lot = line.lot_id
        if lot and "expiration_date" in lot._fields:
            return lot.expiration_date
        return False

    def _wms_carrier_name(self) -> str:
        """Return the delivery carrier name, or ``''`` when ``delivery`` is absent."""
        self.ensure_one()
        if "carrier_id" not in self._fields:
            return ""
        carrier = self.carrier_id
        return carrier.display_name if carrier else ""

    def _wms_delivery_partner(self):
        """Partner to print as the ship-to address.

        Prefers an explicit ``delivery`` type child contact of the picking
        partner, falling back to the picking partner itself.
        """
        self.ensure_one()
        partner = self.partner_id
        if not partner:
            return partner
        children = partner.child_ids.filtered(lambda p: p.type == "delivery")
        return children[0] if children else partner

    def _wms_line_qty(self, line) -> float:
        """Quantity of ``line`` expressed in the product's own UoM."""
        if "quantity_product_uom" in line._fields and line.quantity_product_uom:
            return line.quantity_product_uom
        return line.quantity or 0.0

    # ------------------------------------------------------------------
    # 1. Picking list — walk path
    # ------------------------------------------------------------------
    def _wms_pick_lines(self):
        """Return ``stock.move.line`` records sorted along an optimised walk path.

        Ordering key: source ``location_id.complete_name`` (case-folded), then
        product ``default_code``, then product display name, then id. This is
        deliberately a pure sort so a warehouse-specific implementation (aisle
        serpentine, zone sequencing, …) can override it later.
        """
        self.ensure_one()
        lines = self.move_line_ids.filtered(lambda ml: ml.product_id)
        if not lines:
            return self.env["stock.move.line"].browse()
        return lines.sorted(
            key=lambda ml: (
                (ml.location_id.complete_name or "").upper(),
                (ml.product_id.default_code or _NO_CODE),
                (ml.product_id.display_name or ""),
                ml.id,
            )
        )

    def _wms_pick_rows(self) -> list[dict]:
        """Walk-path rows enriched for the picking-list template."""
        self.ensure_one()
        rows: list[dict] = []
        for seq, line in enumerate(self._wms_pick_lines(), start=1):
            location = line.location_id
            item_value = self._wms_item_barcode_value(line.product_id, line.lot_id)
            rows.append(
                {
                    "seq": seq,
                    # Line-item barcode: what the handheld scans back on this
                    # row (lot when tracked, else EAN, else internal ref).
                    "item_barcode_value": item_value,
                    "item_barcode": self._wms_item_barcode_src(item_value),
                    "line": line,
                    "location": location,
                    "location_name": location.complete_name or location.display_name or "",
                    "location_qr": self._wms_barcode_src(
                        location.barcode or location.complete_name or "",
                        "QR",
                        width=120,
                        height=120,
                    ),
                    "product": line.product_id,
                    "default_code": line.product_id.default_code or "",
                    "lot_name": line.lot_id.name or line.lot_name or "",
                    "expiry": self._wms_line_expiry(line),
                    "qty": line.quantity or 0.0,
                    "uom_name": line.product_uom_id.display_name or "",
                }
            )
        return rows

    def _wms_pick_totals(self) -> dict:
        """Footer totals for the picking list."""
        self.ensure_one()
        rows = self._wms_pick_rows()
        return {
            "line_count": len(rows),
            "total_qty": sum(r["qty"] for r in rows),
        }

    # ------------------------------------------------------------------
    # 2. Packing list — one block per package
    # ------------------------------------------------------------------
    def _wms_package_block(self, package, lines) -> dict:
        """Build a single packing block for ``package`` (may be an empty recordset)."""
        self.ensure_one()
        package_type = package.package_type_id
        dims = (
            (
                package_type.packaging_length or 0.0,
                package_type.width or 0.0,
                package_type.height or 0.0,
            )
            if package_type
            else (0.0, 0.0, 0.0)
        )
        net_weight = 0.0
        for line in lines:
            weight = getattr(line.product_id, "weight", 0.0) or 0.0
            net_weight += weight * self._wms_line_qty(line)
        base_weight = (package_type.base_weight or 0.0) if package_type else 0.0
        name = package.name if package else "Loose / unpacked"
        return {
            "package": package if package else False,
            "name": name,
            "package_type": package_type if package_type else False,
            "package_type_name": package_type.display_name if package_type else "",
            "dims": dims,
            "net_weight": net_weight,
            "gross_weight": net_weight + base_weight,
            "max_weight": (package_type.max_weight or 0.0) if package_type else 0.0,
            "lines": lines,
            "barcode_code128": wms_barcode_url(package.name, "Code128", 500, 90) if package else "",
            "barcode_qr": wms_barcode_url(package.name, "QR", 130, 130) if package else "",
        }

    def _wms_packing_blocks(self) -> list[dict]:
        """Return one dict per destination package plus a trailing loose block.

        Each dict has: ``package`` (``stock.package`` record or ``False``),
        ``name``, ``package_type``, ``dims`` (length, width, height),
        ``net_weight``, ``gross_weight`` (net + ``package_type.base_weight``),
        ``max_weight``, ``lines`` (``stock.move.line`` recordset) and the two
        barcode image URLs.
        """
        self.ensure_one()
        lines = self.move_line_ids.filtered(lambda ml: ml.product_id)
        blocks: list[dict] = []
        packages = lines.mapped("result_package_id").sorted(key=lambda p: (p.name or "", p.id))
        for package in packages:
            pkg_lines = lines.filtered(lambda ml, p=package: ml.result_package_id == p)
            blocks.append(self._wms_package_block(package, pkg_lines))
        loose = lines.filtered(lambda ml: not ml.result_package_id)
        if loose:
            blocks.append(self._wms_package_block(self.env["stock.package"].browse(), loose))
        return blocks

    def _wms_packing_totals(self) -> dict:
        """Footer totals for the packing list (kept out of QWeb on purpose)."""
        self.ensure_one()
        blocks = self._wms_packing_blocks()
        return {
            "package_count": sum(1 for b in blocks if b["package"]),
            "block_count": len(blocks),
            "net_weight": sum(b["net_weight"] for b in blocks),
            "gross_weight": sum(b["gross_weight"] for b in blocks),
        }

    # ------------------------------------------------------------------
    # 3. Barcode list — scan sheet
    # ------------------------------------------------------------------
    def _wms_barcode_rows(self) -> list[dict]:
        """Every distinct package + product barcode of the shipment.

        Each row: ``kind`` (``package``/``product``), ``label`` (human title),
        ``value`` (the scannable payload), ``qr_src`` and ``code128_src``.
        Products without a barcode fall back to their internal reference so the
        sheet still gives the picker something to key in.
        """
        self.ensure_one()
        lines = self.move_line_ids.filtered(lambda ml: ml.product_id)
        rows: list[dict] = []
        seen: set[tuple[str, str]] = set()

        def _add(kind: str, label: str, value: str) -> None:
            value = (value or "").strip()
            if not value or (kind, value) in seen:
                return
            seen.add((kind, value))
            rows.append(
                {
                    "kind": kind,
                    "label": label,
                    "value": value,
                    "qr_src": wms_barcode_url(value, "QR", 150, 150),
                    "code128_src": wms_barcode_url(value, "Code128", 450, 80),
                }
            )

        for package in lines.mapped("result_package_id").sorted(key=lambda p: (p.name or "", p.id)):
            _add("package", package.name or "", package.name or "")
        for product in lines.mapped("product_id").sorted(key=lambda p: (p.default_code or _NO_CODE, p.id)):
            _add("product", product.display_name or "", product.barcode or product.default_code or "")
        return rows

    def _wms_barcode_row_pairs(self, per_row: int = 2) -> list[list]:
        """Chunk :meth:`_wms_barcode_rows` into fixed-width grid rows.

        Chunking in Python keeps the scan-sheet template a plain nested
        ``t-foreach`` (no index arithmetic inside QWeb).
        """
        self.ensure_one()
        rows = self._wms_barcode_rows()
        size = per_row if per_row > 0 else 2
        return [rows[i : i + size] for i in range(0, len(rows), size)]
