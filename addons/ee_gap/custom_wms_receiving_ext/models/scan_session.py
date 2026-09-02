# -*- coding: utf-8 -*-
"""Serial/IMEI capture + post-apply enrichment of lots created by the scan flow.

``custom_barcode.action_apply_to_picking`` creates/links ``stock.lot``
records but drops the GS1 expiration date (AI 17) it already parsed, and
knows nothing about supplier batch references. Rather than fork the shared
addon, we run after it and enrich every lot the OK scan lines point at.

Two serial-specific gaps are closed here as well, because the shared addon
only ever resolves a lot from GS1 AI 10:

* **GS1 AI 21 (serial)** — for a serial-tracked product the serial must
  become the ``stock.lot`` name. Without this the lot ends up named after
  the whole element string (``0189…21356938…``).
* **Bare IMEI scan** — handhelds emit the raw 14–16 digit IMEI, which
  resolves to neither a product barcode nor an existing lot. When the
  picking has exactly one serial-tracked product still short of demand, the
  scan is attributed to it instead of being parked as ``not_found``.
"""

from __future__ import annotations

import logging
import re

from odoo import fields, models

_logger = logging.getLogger(__name__)

# IMEI is 15 digits; allow 14 (IMEI without check digit) to 16 (IMEISV).
_IMEI_RE = re.compile(r"^\d{14,16}$")


class CustomBarcodeScanSession(models.Model):
    _inherit = "custom.barcode.scan.session"

    # ------------------------------------------------------------------
    # Scan-time: serial capture
    # ------------------------------------------------------------------

    def _wms_serial_candidate(self):
        """The picking's serial-tracked product, if there is exactly one."""
        self.ensure_one()
        if not self.picking_id:
            return self.env["product.product"]
        products = self.picking_id.move_ids.product_id.filtered(lambda p: p.tracking == "serial")
        return products if len(products) == 1 else self.env["product.product"].browse()

    def on_barcode_scanned(self, barcode):
        res = super().on_barcode_scanned(barcode)
        line = self.line_ids.sorted(key=lambda l: l.id)[-1:]
        if not line:
            return res
        gs1 = line.get_gs1_dict()
        serial = gs1.get("serial")

        # Bare IMEI: no product, no lot — attribute it to the sole serial
        # product on the picking rather than losing the scan.
        if line.status == "not_found" and not line.product_id and _IMEI_RE.match((barcode or "").strip()):
            product = self._wms_serial_candidate()
            if product:
                serial = barcode.strip()
                line.write({"product_id": product.id, "status": "ok", "quantity": 1.0})

        if not serial or not line.product_id or line.product_id.tracking != "serial":
            return res
        if line.lot_id:
            return res
        Lot = self.env["stock.lot"]
        lot = Lot.search([("name", "=", serial), ("product_id", "=", line.product_id.id)], limit=1)
        if not lot:
            lot = Lot.create(
                {
                    "name": serial,
                    "product_id": line.product_id.id,
                    "company_id": (self.picking_id.company_id or self.env.company).id,
                }
            )
        # One serial = one unit; a GS1 weight AI must not inflate it.
        line.write({"lot_id": lot.id, "quantity": 1.0})
        return res

    def _wms_normalise_serial_move_lines(self):
        """One serial = one unit, on its own move line.

        Odoo 19 pre-fills an incoming move line with the full demand, and the
        shared apply routine *adds* the scanned quantity on top — a scanned
        serial would end up on a line of 2+ units, which stock refuses to
        validate. Lines of a scanned serial product are therefore pinned to 1
        when they carry a lot, and zeroed when they do not (an unscanned
        serial cannot be received anyway).
        """
        for rec in self:
            if not rec.picking_id:
                continue
            products = rec.line_ids.filtered(lambda l: l.status == "ok").product_id.filtered(
                lambda p: p.tracking == "serial"
            )
            if not products:
                continue
            for ml in rec.picking_id.move_line_ids.filtered(lambda l: l.product_id in products):
                target = 1.0 if ml.lot_id else 0.0
                if ml.quantity != target:
                    ml.quantity = target

    # ------------------------------------------------------------------
    # Apply-time: lot enrichment
    # ------------------------------------------------------------------

    def _wms_zero_prefilled_quantities(self):
        """Scans SET the received quantity — they must not stack on the demand.

        Odoo 19 pre-fills an incoming move line with the full demand at
        confirm, and the shared apply routine *adds* each scan on top: a
        receipt of 18 demanded units scanned as 18 was booked as 36, i.e.
        every scanned GR silently over-received. Zero the pre-filled lines of
        the scanned products first (the same guard the receipt-import wizard
        already applies), so the physical count wins.

        Lines already filled by *another* scan session are left alone — a
        receipt may be scanned by several operators in sequence, and their
        quantities must add up. Re-applying the same session recomputes only
        its own lines.
        """
        for rec in self:
            if not rec.picking_id or rec.picking_id.state in ("done", "cancel"):
                continue
            products = rec.line_ids.filtered(lambda l: l.status == "ok").product_id
            if not products:
                continue
            for ml in rec.picking_id.move_line_ids.filtered(lambda l: l.product_id in products):
                if ml.wms_scan_session_id and ml.wms_scan_session_id != rec:
                    continue
                if ml.quantity:
                    ml.quantity = 0.0

    def _wms_stamp_scan_provenance(self):
        """Mark the move lines this session filled, so the next one adds to them."""
        for rec in self:
            if not rec.picking_id:
                continue
            products = rec.line_ids.filtered(lambda l: l.status == "ok").product_id
            lines = rec.picking_id.move_line_ids.filtered(
                lambda l: l.product_id in products and l.quantity and not l.wms_scan_session_id
            )
            if lines:
                lines.wms_scan_session_id = rec.id

    def action_apply_to_picking(self):
        self._wms_zero_prefilled_quantities()
        for rec in self:
            # Serial lines that carry a GS1 serial but no lot yet (created
            # programmatically rather than through on_barcode_scanned).
            for line in rec.line_ids.filtered(
                lambda l: l.status == "ok" and l.product_id.tracking == "serial" and not l.lot_id
            ):
                serial = line.get_gs1_dict().get("serial")
                if not serial:
                    continue
                Lot = self.env["stock.lot"]
                lot = Lot.search(
                    [("name", "=", serial), ("product_id", "=", line.product_id.id)], limit=1
                ) or Lot.create(
                    {
                        "name": serial,
                        "product_id": line.product_id.id,
                        "company_id": (rec.picking_id.company_id or self.env.company).id,
                    }
                )
                line.write({"lot_id": lot.id, "quantity": 1.0})
        res = super().action_apply_to_picking()
        self._wms_normalise_serial_move_lines()
        self._wms_stamp_scan_provenance()
        Lot = self.env["stock.lot"]
        for rec in self:
            if not rec.picking_id:
                continue
            for line in rec.line_ids.filtered(lambda l: l.status == "ok" and l.product_id):
                if line.product_id.tracking not in ("lot", "serial"):
                    continue
                gs1 = line.get_gs1_dict()
                lot = line.lot_id
                if not lot:
                    lot_name = gs1.get("lot") or line.raw_barcode
                    if not lot_name:
                        continue
                    lot = Lot.search(
                        [("name", "=", lot_name), ("product_id", "=", line.product_id.id)],
                        limit=1,
                    )
                if not lot:
                    continue
                vals = {}
                exp_date = gs1.get("exp_date")
                if exp_date:
                    # product_expiry pre-fills expiration_date from the
                    # product's expiration_time on lot creation; the explicit
                    # GS1 date from the physical label must override it.
                    exp = fields.Date.to_date(exp_date)
                    if exp:
                        vals["expiration_date"] = fields.Datetime.to_datetime(exp)
                batch_ref = line.supplier_batch_ref or gs1.get("lot")
                if batch_ref and not lot.supplier_batch_ref:
                    vals["supplier_batch_ref"] = batch_ref
                if vals:
                    lot.write(vals)
                    _logger.info(
                        "scan session %s: enriched lot %s with %s",
                        rec.name,
                        lot.name,
                        vals,
                    )
        return res
