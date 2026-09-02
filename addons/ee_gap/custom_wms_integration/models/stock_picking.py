# -*- coding: utf-8 -*-
"""Business-object hooks that feed the outbox.

The single rule this file obeys: **a host integration must never be able to
fail a warehouse transaction**. Every enqueue goes through
``wms.integration.event._safe_enqueue`` (savepoint + swallow), and the
``button_validate`` override enqueues only *after* super() succeeded.
"""

from __future__ import annotations

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

#: picking_type_id.code -> outbound event type
_CODE_TO_EVENT = {
    "incoming": "goods_receipt",
    "outgoing": "goods_issue",
    "internal": "putaway_done",
}


class StockPicking(models.Model):
    _inherit = "stock.picking"

    wms_external_ref = fields.Char(
        string="WMS/Host Reference",
        index=True,
        copy=False,
        help="external_ref supplied by the host on /api/wms/asn or /api/wms/do. "
        "The idempotency key for inbound ASN/DO messages.",
    )
    # Many2many (not One2many): the outbox links back generically via
    # res_model/res_id, so there is no inverse column to point an o2m at.
    wms_event_ids = fields.Many2many(
        "wms.integration.event",
        compute="_compute_wms_event_ids",
        string="WMS Events",
    )
    wms_event_count = fields.Integer(compute="_compute_wms_event_ids")

    def _compute_wms_event_ids(self):
        Event = self.env["wms.integration.event"]
        for picking in self:
            events = Event.sudo().search([("res_model", "=", "stock.picking"), ("res_id", "=", picking.id)])
            picking.wms_event_ids = events
            picking.wms_event_count = len(events)

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def button_validate(self):
        result = super().button_validate()
        # super() may return a wizard action (backorder / immediate transfer);
        # only pickings that actually reached "done" are reportable events.
        for picking in self:
            if picking.state != "done":
                continue
            # Outer guard: the savepoint keeps a broken payload builder from
            # poisoning the cursor, the bare except keeps it from rolling back a
            # validated transfer. _safe_enqueue guards the INSERT itself.
            try:
                with self.env.cr.savepoint():
                    picking._wms_enqueue_validation_events()
            except Exception:  # noqa: BLE001 - integration must never fail the transfer
                _logger.exception("WMS outbox hook failed for picking %s — transfer stands", picking.name)
        return result

    def _wms_enqueue_validation_events(self):
        """Emit every event this validated picking implies. Never raises."""
        self.ensure_one()
        Event = self.env["wms.integration.event"]
        code = self.picking_type_id.code
        event_type = _CODE_TO_EVENT.get(code)
        if event_type:
            Event._safe_enqueue(
                event_type,
                record=self,
                payload=self._wms_picking_payload(event_type),
                company=self.company_id,
            )
        # Outbound pickings additionally report the pick confirmation and any
        # packages that were built on them.
        if code == "outgoing":
            Event._safe_enqueue(
                "pick_confirmed",
                record=self,
                payload=self._wms_picking_payload("pick_confirmed"),
                company=self.company_id,
            )
        self._wms_enqueue_package_events()
        return True

    def _wms_enqueue_package_events(self):
        """One ``pack_created`` event per result package on this picking.

        Odoo 19 renamed ``stock.quant.package`` to ``stock.package``; the model
        is looked up defensively so this keeps working if a tenant is still on
        an older bridge module.
        """
        self.ensure_one()
        Event = self.env["wms.integration.event"]
        Mapping = self.env["wms.integration.mapping"]
        packages = self.move_line_ids.mapped("result_package_id")
        for package in packages:
            lines = self.move_line_ids.filtered(lambda ml, p=package: ml.result_package_id == p)
            payload = {
                "picking": self.name,
                "external_ref": self.wms_external_ref or self.name,
                "package": package.name,
                "package_type": package.package_type_id.name if package.package_type_id else None,
                "lines": [
                    {
                        "sku": Mapping._external_code_for(ml.product_id),
                        "qty": ml.quantity,
                        "uom": ml.product_uom_id.name if ml.product_uom_id else None,
                        "lot": ml.lot_id.name or ml.lot_name or None,
                    }
                    for ml in lines
                ],
            }
            Event._safe_enqueue(
                "pack_created",
                record=self,
                payload=payload,
                external_ref="%s/%s" % (self.name, package.name),
                company=self.company_id,
            )
        return True

    # ------------------------------------------------------------------
    # Payload
    # ------------------------------------------------------------------

    def _wms_picking_payload(self, event_type):
        self.ensure_one()
        Mapping = self.env["wms.integration.mapping"]
        return {
            "event_type": event_type,
            "picking": self.name,
            "picking_type": self.picking_type_id.code,
            "external_ref": self.wms_external_ref or self.name,
            "origin": self.origin or None,
            "partner": Mapping._external_code_for(self.partner_id) if self.partner_id else None,
            "warehouse_code": self.picking_type_id.warehouse_id.code or None,
            "date_done": fields.Datetime.to_string(self.date_done) if self.date_done else None,
            "lines": [
                {
                    "sku": Mapping._external_code_for(ml.product_id),
                    "qty": ml.quantity,
                    "uom": ml.product_uom_id.name if ml.product_uom_id else None,
                    "lot": ml.lot_id.name or ml.lot_name or None,
                    "location_code": Mapping._external_code_for(ml.location_id),
                    "location_dest_code": Mapping._external_code_for(ml.location_dest_id),
                    "package": ml.result_package_id.name if ml.result_package_id else None,
                }
                for ml in self.move_line_ids
            ],
        }

    # ------------------------------------------------------------------
    # Inbound ASN / DO upsert
    # ------------------------------------------------------------------

    @api.model
    def _wms_upsert_from_host(self, payload, direction):
        """Create-or-update a draft picking from an ASN (incoming) or DO (outgoing).

        Idempotent on ``external_ref``: a second POST with the same reference
        updates the existing draft instead of creating a second picking. Once the
        picking has left ``draft``/``confirmed`` it is treated as frozen and the
        payload is ignored (the host cannot rewrite work already in progress).

        Returns ``(picking, created, warnings)``.
        """
        assert direction in ("incoming", "outgoing")
        Mapping = self.env["wms.integration.mapping"]
        warnings = []

        external_ref = (payload.get("external_ref") or "").strip()
        if not external_ref:
            raise ValueError("external_ref is required")

        picking_type = self._wms_picking_type(payload.get("warehouse_code"), direction)
        if not picking_type:
            raise ValueError("no %s operation type for warehouse_code=%r" % (direction, payload.get("warehouse_code")))
        company = picking_type.company_id or self.env.company

        existing = self.sudo().search(
            [("wms_external_ref", "=", external_ref), ("company_id", "=", company.id)],
            limit=1,
        )
        if existing and existing.state not in ("draft", "confirmed"):
            return existing, False, ["picking already in state %s; payload ignored" % existing.state]

        partner = (
            Mapping._resolve(payload.get("partner_ref"), "res.partner", company)
            if payload.get("partner_ref")
            else self.env["res.partner"].browse()
        )
        if payload.get("partner_ref") and not partner:
            warnings.append("unknown partner_ref %s" % payload["partner_ref"])

        vals = {
            "picking_type_id": picking_type.id,
            "location_id": picking_type.default_location_src_id.id or self.env.ref("stock.stock_location_suppliers").id,
            "location_dest_id": picking_type.default_location_dest_id.id
            or self.env.ref("stock.stock_location_customers").id,
            "origin": payload.get("origin") or external_ref,
            "wms_external_ref": external_ref,
            "company_id": company.id,
        }
        if partner:
            vals["partner_id"] = partner.id
        if payload.get("expected_date"):
            vals["scheduled_date"] = payload["expected_date"]

        if existing:
            picking = existing
            picking.sudo().write(vals)
            picking.sudo().move_ids.filtered(lambda m: m.state == "draft").unlink()
            created = False
        else:
            picking = self.sudo().create(vals)
            created = True

        warnings += picking._wms_sync_lines(payload.get("lines") or [], company)
        return picking, created, warnings

    @api.model
    def _wms_picking_type(self, warehouse_code, direction):
        """Resolve the operation type for a host warehouse code."""
        Warehouse = self.env["stock.warehouse"].sudo()
        warehouse = Warehouse.browse()
        code = (warehouse_code or "").strip()
        if code:
            warehouse = Warehouse.search(["|", ("code", "=", code), ("name", "=", code)], limit=1)
        if not warehouse:
            warehouse = Warehouse.search([("company_id", "=", self.env.company.id)], limit=1)
        if not warehouse:
            return self.env["stock.picking.type"].browse()
        field = "in_type_id" if direction == "incoming" else "out_type_id"
        return warehouse[field]

    def _wms_sync_lines(self, lines, company):
        """(Re)build the draft moves of this picking from a host line list."""
        self.ensure_one()
        Mapping = self.env["wms.integration.mapping"]
        Move = self.env["stock.move"].sudo()
        MoveLine = self.env["stock.move.line"].sudo()
        Uom = self.env["uom.uom"].sudo()
        warnings = []

        for line in lines:
            sku = (line.get("sku") or "").strip()
            product = Mapping._resolve(sku, "product.product", company)
            if not product:
                warnings.append("unknown sku %s (line skipped)" % sku)
                continue
            uom = product.uom_id
            if line.get("uom"):
                found = Uom.search([("name", "=", line["uom"])], limit=1)
                if found:
                    uom = found
                else:
                    warnings.append("unknown uom %s on sku %s; used %s" % (line["uom"], sku, uom.name))
            try:
                qty = float(line.get("qty") or 0.0)
            except (TypeError, ValueError):
                warnings.append("bad qty on sku %s (line skipped)" % sku)
                continue
            # Odoo 19 removed stock.move.name — never pass it. The human label
            # lives in reference / description_picking.
            move = Move.create(
                {
                    "reference": self.name,
                    "description_picking": product.display_name,
                    "product_id": product.id,
                    "product_uom": uom.id,
                    "product_uom_qty": qty,
                    "picking_id": self.id,
                    "location_id": self.location_id.id,
                    "location_dest_id": self.location_dest_id.id,
                    "company_id": company.id,
                }
            )
            lot = (line.get("lot") or "").strip()
            if lot:
                ml_vals = {
                    "move_id": move.id,
                    "picking_id": self.id,
                    "product_id": product.id,
                    "product_uom_id": uom.id,
                    "quantity": qty,
                    "lot_name": lot,
                    "location_id": self.location_id.id,
                    "location_dest_id": self.location_dest_id.id,
                    "company_id": company.id,
                }
                # expiration_date only exists when product_expiry is installed.
                if line.get("expiry") and "expiration_date" in MoveLine._fields:
                    ml_vals["expiration_date"] = line["expiry"]
                elif line.get("expiry"):
                    warnings.append("expiry ignored on sku %s: product_expiry not installed" % sku)
                try:
                    MoveLine.create(ml_vals)
                except Exception as exc:  # noqa: BLE001 - a lot hint must not kill the ASN
                    warnings.append("lot %s not applied on sku %s: %s" % (lot, sku, exc))
                    _logger.warning("WMS ASN lot line rejected: %s", exc)
        return warnings
