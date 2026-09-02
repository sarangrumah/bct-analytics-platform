# -*- coding: utf-8 -*-
"""stock.picking / stock.picking.type / stock.move — the inbound QC gate.

Flow
----
1. A receipt on a picking type with ``wms_qc_required`` lands its goods in the
   warehouse QC area (``wms_qc_location_id``) instead of stock.
2. Because that area is reservation-blocked, nothing outbound can touch the
   goods — requirement "barang di inbound tidak dapat dipesan ke outbound".
3. QC passes -> ``action_wms_qc_pass`` builds one internal transfer QC -> stock.
   That transfer carries ``wms_allow_blocked_locations`` so it *can* pick the
   goods up, and the putaway engine slots each line into a real bin.
4. QC fails -> the goods stay quarantined and the picking records who failed it.
"""

from __future__ import annotations

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .stock_quant import BYPASS_CTX

_logger = logging.getLogger(__name__)

QC_STATES = [
    ("not_required", "Not Required"),
    ("pending", "Pending Inspection"),
    ("passed", "Passed"),
    ("failed", "Failed"),
]


class StockPickingType(models.Model):
    _inherit = "stock.picking.type"

    wms_qc_required = fields.Boolean(
        string="Require Inbound QC",
        default=False,
        help="Receipts of this type land in the QC area and must be inspected before the goods become pickable.",
    )
    wms_qc_location_id = fields.Many2one(
        "stock.location",
        string="QC Area",
        domain="[('usage', '=', 'internal')]",
        check_company=True,
        help="Where inspected-pending goods are received. Defaults to the "
        "picking type's destination location when unset.",
    )


class StockPicking(models.Model):
    _inherit = "stock.picking"

    wms_qc_state = fields.Selection(
        QC_STATES,
        string="QC Status",
        default="not_required",
        copy=False,
        tracking=True,
        index=True,
    )
    wms_qc_user_id = fields.Many2one("res.users", string="Inspected By", readonly=True, copy=False)
    wms_qc_date = fields.Datetime(string="Inspected On", readonly=True, copy=False)
    wms_qc_notes = fields.Text(string="QC Notes", copy=False)
    wms_qc_release_picking_id = fields.Many2one(
        "stock.picking",
        string="QC Release Transfer",
        readonly=True,
        copy=False,
        help="Internal transfer that moved the passed goods out of quarantine.",
    )
    wms_qc_release_ok = fields.Boolean(
        string="Cleared to Leave Quarantine",
        default=False,
        copy=False,
        readonly=True,
        help="Set on the internal transfer that QC has authorised. Only such a "
        "transfer may reserve stock out of a quarantined location — otherwise "
        "the second leg of a two-step receipt would silently bypass the gate.",
    )

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------

    @api.onchange("picking_type_id")
    def _onchange_picking_type_qc(self):
        for rec in self:
            ptype = rec.picking_type_id
            if ptype and ptype.code == "incoming" and ptype.wms_qc_required:
                rec.wms_qc_state = "pending"
                qc_loc = ptype.wms_qc_location_id
                if qc_loc:
                    rec.location_dest_id = qc_loc.id
            elif rec.wms_qc_state == "pending":
                rec.wms_qc_state = "not_required"

    @api.model_create_multi
    def create(self, vals_list):
        pickings = super().create(vals_list)
        for pick in pickings:
            ptype = pick.picking_type_id
            if ptype.code == "incoming" and ptype.wms_qc_required and pick.wms_qc_state == "not_required":
                vals = {"wms_qc_state": "pending"}
                if ptype.wms_qc_location_id:
                    vals["location_dest_id"] = ptype.wms_qc_location_id.id
                pick.write(vals)
        return pickings

    # ------------------------------------------------------------------
    # QC actions
    # ------------------------------------------------------------------

    def _wms_qc_group(self) -> str:
        return "custom_wms_inbound_qc.group_wms_qc_inspector"

    def _wms_check_qc_rights(self):
        if not self.env.user.has_group(self._wms_qc_group()):
            raise UserError(_("Only QC inspectors may pass or fail an inbound inspection."))

    def action_wms_qc_pass(self):
        """Release inspected goods from quarantine into pickable stock."""
        self._wms_check_qc_rights()
        for pick in self:
            if pick.wms_qc_state != "pending":
                raise UserError(
                    _("%(name)s is not pending inspection (status: %(state)s).")
                    % {"name": pick.name, "state": pick.wms_qc_state}
                )
            if pick.state != "done":
                raise UserError(
                    _("Validate the receipt %s before passing QC — there is nothing in quarantine yet.") % pick.name
                )
            pick.write(
                {
                    "wms_qc_state": "passed",
                    "wms_qc_user_id": self.env.user.id,
                    "wms_qc_date": fields.Datetime.now(),
                }
            )
            release = pick._wms_create_release_transfer()
            if release:
                pick.wms_qc_release_picking_id = release.id
                pick.message_post(
                    body=_("QC passed. Release transfer %s created.") % release.name,
                    message_type="comment",
                )
            else:
                pick.message_post(body=_("QC passed. No release transfer was needed."), message_type="comment")
        return True

    def action_wms_qc_fail(self):
        self._wms_check_qc_rights()
        for pick in self:
            if pick.wms_qc_state != "pending":
                raise UserError(
                    _("%(name)s is not pending inspection (status: %(state)s).")
                    % {"name": pick.name, "state": pick.wms_qc_state}
                )
            pick.write(
                {
                    "wms_qc_state": "failed",
                    "wms_qc_user_id": self.env.user.id,
                    "wms_qc_date": fields.Datetime.now(),
                }
            )
            pick.message_post(
                body=_(
                    "QC FAILED. Goods remain quarantined in %(loc)s and cannot be "
                    "reserved for outbound. Notes: %(notes)s"
                )
                % {"loc": pick.location_dest_id.display_name, "notes": pick.wms_qc_notes or _("(none)")},
                message_type="comment",
            )
            pick._wms_raise_quality_alert()
        return True

    def _wms_raise_quality_alert(self):
        """Open an NCR when the quality module is present. Optional by design."""
        self.ensure_one()
        Alert = self.env.get("quality.alert")
        if Alert is None:
            return False
        try:
            return Alert.create(
                {
                    "name": _("Inbound QC failure: %s") % self.name,
                    "description": self.wms_qc_notes or "",
                    "picking_id": self.id,
                    "company_id": self.company_id.id,
                }
            )
        except Exception as exc:  # pragma: no cover - the alert must never block QC
            _logger.warning("Could not raise quality alert for picking %s: %s", self.name, exc)
            return False

    # ------------------------------------------------------------------
    # Release transfer
    # ------------------------------------------------------------------

    def _wms_release_destination(self):
        """Where released goods go: the warehouse's pickable stock location."""
        self.ensure_one()
        warehouse = self.picking_type_id.warehouse_id
        if warehouse and warehouse.lot_stock_id:
            return warehouse.lot_stock_id
        return self.env["stock.location"]

    def _wms_existing_release_transfer(self):
        """The route-generated second leg of a multi-step receipt, if any.

        A two-step reception already creates an ``Input -> Stock`` internal
        transfer chained off the receipt's moves. Re-creating one here would
        double the goods, so the route's own picking is adopted instead.
        """
        self.ensure_one()
        chained = self.move_ids.move_dest_ids.filtered(lambda m: m.state not in ("done", "cancel") and m.picking_id)
        return chained.picking_id.filtered(lambda p: p.picking_type_id.code == "internal")

    def _wms_create_release_transfer(self):
        """Release the goods: adopt the route's transfer, or build one."""
        self.ensure_one()
        existing = self._wms_existing_release_transfer()
        if existing:
            existing.write({"wms_qc_release_ok": True})
            existing.action_assign()
            return existing[:1]
        dest = self._wms_release_destination()
        if not dest:
            raise UserError(
                _("No stock location found for warehouse %s — cannot release the goods.")
                % (self.picking_type_id.warehouse_id.display_name or "?")
            )
        int_type = self.picking_type_id.warehouse_id.int_type_id
        if not int_type:
            raise UserError(_("Warehouse %s has no internal transfer type.") % self.picking_type_id.warehouse_id.name)

        blocked_ids = set(self.env["stock.location"]._wms_blocked_location_ids())
        move_vals = []
        for line in self.move_line_ids:
            if line.location_dest_id.id not in blocked_ids:
                # Already outside quarantine (partial manual putaway) — skip.
                continue
            qty = line.quantity if "quantity" in line._fields else line.qty_done
            if not qty:
                continue
            move_vals.append(
                {
                    "product_id": line.product_id.id,
                    "product_uom": line.product_uom_id.id,
                    "product_uom_qty": qty,
                    "location_id": line.location_dest_id.id,
                    "location_dest_id": dest.id,
                    "company_id": self.company_id.id,
                }
            )
        if not move_vals:
            return self.env["stock.picking"]

        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": int_type.id,
                "location_id": self.location_dest_id.id,
                "location_dest_id": dest.id,
                "origin": _("QC release %s") % self.name,
                "company_id": self.company_id.id,
                "wms_qc_release_ok": True,
                "move_ids": [(0, 0, vals) for vals in move_vals],
            }
        )
        # The source bins are quarantined; this transfer is precisely the thing
        # allowed to reserve from them.
        picking = picking.with_context(**{BYPASS_CTX: True})
        picking.action_confirm()
        picking.action_assign()
        return picking

    # ------------------------------------------------------------------
    # Guard rails
    # ------------------------------------------------------------------

    def button_validate(self):
        for pick in self:
            if pick.picking_type_id.code != "outgoing":
                continue
            failed = pick.move_line_ids.filtered(
                lambda ml: (
                    ml.location_id.wms_block_reservation
                    or ml.location_id.id in set(self.env["stock.location"]._wms_blocked_location_ids())
                )
            )
            if failed:
                raise UserError(
                    _(
                        "Delivery %(name)s draws from quarantined location(s): %(locs)s. "
                        "Pass inbound QC before shipping these goods."
                    )
                    % {
                        "name": pick.name,
                        "locs": ", ".join(sorted(set(failed.mapped("location_id.display_name")))),
                    }
                )
        return super().button_validate()


class StockMove(models.Model):
    _inherit = "stock.move"

    def _action_assign(self, force_qty=False):
        """Let a QC-release transfer reserve out of quarantined bins."""
        blocked = set(self.env["stock.location"]._wms_blocked_location_ids())
        if not blocked or self.env.context.get(BYPASS_CTX):
            return super()._action_assign(force_qty=force_qty)
        # Only a QC-authorised transfer may draw out of quarantine. Without the
        # explicit flag the second leg of a two-step receipt would reserve the
        # goods the moment they land, and the gate would be decorative.
        release = self.filtered(
            lambda m: (
                m.picking_type_id.code == "internal"
                and m.picking_id.wms_qc_release_ok
                and m.location_id.id in blocked
                and m.location_dest_id.id not in blocked
            )
        )
        rest = self - release
        res = None
        if release:
            res = super(StockMove, release.with_context(**{BYPASS_CTX: True}))._action_assign(force_qty=force_qty)
        if rest:
            res = super(StockMove, rest)._action_assign(force_qty=force_qty)
        return res


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    def _is_incoming(self) -> bool:
        """Decide where ``custom_wms_putaway`` is allowed to slot goods.

        Two corrections to the plain "is this a receipt?" answer:

        * A receipt still pending QC is NOT a putaway event. Auto-apply would
          rewrite the destination to a storage bin and walk the goods straight
          past quarantine — the gate would hold nothing. Slotting waits.
        * The QC release transfer IS one, even though it is an internal move:
          that is where the real bin decision happens.
        """
        self.ensure_one()
        if super()._is_incoming():
            return self.picking_id.wms_qc_state != "pending"
        ptype = self.picking_id.picking_type_id
        if not ptype or ptype.code != "internal":
            return False
        blocked = set(self.env["stock.location"]._wms_blocked_location_ids())
        return bool(self.location_id.id in blocked and self.location_dest_id.id not in blocked)
