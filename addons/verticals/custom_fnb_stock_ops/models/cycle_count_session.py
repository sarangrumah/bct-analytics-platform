# -*- coding: utf-8 -*-
"""Stock opname: ESB half of a cycle-count session.

The counting workflow itself is ``custom_wms_cycle_count`` — plan, session,
line, the counting UX and the supervisor variance gate all already exist. This
adds only what ESB needs:

- a session is scoped to an ESB branch + location;
- expected quantities come from ``custom.esb.stock.snapshot``, not ``stock.quant``;
- closing the session emits **one** ESB Item Journal for the whole count.

The single most important detail is in :meth:`_esb_journal_payload`:
``itemJournalDetails[].qty`` is the **signed variance** (``counted - expected``),
not the counted quantity. Sending the counted quantity would post the entire
stock balance as an adjustment.
"""

from __future__ import annotations

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CycleCountSession(models.Model):
    _inherit = "custom.cycle.count.session"

    esb_branch_id = fields.Many2one("custom.esb.branch", string="ESB Branch", index=True)
    esb_location_id = fields.Many2one(
        "custom.esb.location",
        string="ESB Location",
        index=True,
        domain="[('branch_id', '=', esb_branch_id)]",
        help="Item journals only accept warehouse- and kitchen-type locations.",
    )
    is_esb_backed = fields.Boolean(compute="_compute_is_esb_backed", store=True, index=True)
    esb_outbox_id = fields.Many2one("custom.esb.outbox", string="ESB Document", readonly=True, copy=False)
    esb_item_journal_num = fields.Char(related="esb_outbox_id.esb_doc_num", string="ESB Item Journal No.", store=True)
    esb_state = fields.Selection(related="esb_outbox_id.state", string="ESB Status")
    esb_stale_snapshot = fields.Boolean(
        compute="_compute_esb_stale_snapshot",
        help="Set when a counted line's expected quantity is older than the configured "
        "freshness window, so the variance may be computed against a stale balance.",
    )

    @api.depends("esb_location_id")
    def _compute_is_esb_backed(self):
        for rec in self:
            rec.is_esb_backed = bool(rec.esb_location_id)

    @api.depends("line_ids", "esb_location_id")
    def _compute_esb_stale_snapshot(self):
        Snapshot = self.env["custom.esb.stock.snapshot"]
        for rec in self:
            if not rec.is_esb_backed:
                rec.esb_stale_snapshot = False
                continue
            rec.esb_stale_snapshot = (
                Snapshot.sudo().search_count(
                    [
                        ("location_id", "=", rec.esb_location_id.id),
                        ("as_of", "<", Snapshot._stale_before()),
                    ]
                )
                > 0
            )

    @api.onchange("esb_branch_id")
    def _onchange_esb_branch(self):
        if self.esb_location_id.branch_id != self.esb_branch_id:
            self.esb_location_id = False

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    def action_generate_lines_from_esb(self):
        """Seed count lines from the ESB stock snapshot for this location.

        Products with no snapshot row are *not* seeded with an expected quantity
        of zero — an unmoved product's balance is unknown, and counting it
        against an assumed zero would post its whole stock as a variance. They
        are still seeded so the counter can record a physical count, but the
        line is flagged and its variance is only meaningful once the expected
        quantity has been verified.
        """
        Line = self.env["custom.cycle.count.line"]
        Snapshot = self.env["custom.esb.stock.snapshot"].sudo()
        for rec in self:
            if not rec.is_esb_backed:
                raise UserError(_("Session %s is not scoped to an ESB location.") % rec.display_name)
            if rec.state not in ("draft", "in_progress"):
                raise UserError(_("Lines can only be generated while the session is draft or in progress."))
            odoo_location = rec.esb_location_id.location_id
            if not odoo_location:
                rec.esb_location_id.action_create_odoo_location()
                odoo_location = rec.esb_location_id.location_id
            snapshots = Snapshot.search([("location_id", "=", rec.esb_location_id.id)])
            existing = set(rec.line_ids.mapped("product_id").ids)
            vals_list = []
            for seq, snap in enumerate(snapshots, start=1):
                if snap.product_id.id in existing:
                    continue
                vals_list.append(
                    {
                        "session_id": rec.id,
                        "sequence": seq * 10,
                        "location_id": odoo_location.id,
                        "product_id": snap.product_id.id,
                        "expected_qty": snap.qty,
                        "status": "pending",
                    }
                )
            if vals_list:
                Line.create(vals_list)
        return True

    def action_refresh_expected_from_esb(self):
        """Re-read the authoritative per-SKU balance right before posting.

        The snapshot cron can be hours old. An opname delta computed against a
        stale expected quantity posts the wrong number into ESB's ledger, so
        this re-reads ``/product/stock-location`` for every counted line.
        """
        Snapshot = self.env["custom.esb.stock.snapshot"].sudo()
        for rec in self:
            if not rec.is_esb_backed:
                continue
            for line in rec.line_ids.filtered(lambda l: l.status in ("counted", "recount_required")):
                snap = Snapshot.refresh_one(rec.esb_location_id, line.product_id)
                if snap:
                    line.expected_qty = snap.qty
        return True

    # ------------------------------------------------------------------
    # Closing → ESB item journal
    # ------------------------------------------------------------------

    def action_close(self):
        res = super().action_close()
        for rec in self.filtered(lambda s: s.is_esb_backed and not s.esb_outbox_id):
            rec._esb_emit_item_journal()
        return res

    def _esb_variance_lines(self):
        """Approved lines with a non-zero variance — the only ones ESB should see.

        Skipped, rejected and zero-variance lines are excluded: a zero-qty
        adjustment line is noise in ESB's ledger, and an unapproved line has not
        passed the supervisor gate.
        """
        self.ensure_one()
        return self.line_ids.filtered(lambda l: l.status == "approved" and l.variance_qty)

    def _esb_emit_item_journal(self):
        """Build one item journal for the whole session and hand it to the outbox."""
        self.ensure_one()
        lines = self._esb_variance_lines()
        if not lines:
            _logger.info("ESB opname %s closed with no variance — nothing to post", self.display_name)
            return False
        payload = self._esb_journal_payload(lines)
        outbox = self.env["custom.esb.outbox"].enqueue(
            "item_journal",
            payload,
            res_model=self._name,
            res_id=self.id,
        )
        self.esb_outbox_id = outbox
        self.message_post(
            body=_("Stock opname pushed to ESB as %s adjustment line(s).") % len(payload["itemJournalDetails"])
        )
        return outbox

    def _esb_journal_payload(self, lines):
        self.ensure_one()
        details = []
        for line in lines:
            detail_id = line.product_id._esb_detail_id("stock")
            if not detail_id:
                raise UserError(
                    _("Product %s has no ESB product detail — run the ESB master sync first.")
                    % line.product_id.display_name
                )
            details.append(
                {
                    "ID": -1,
                    "productDetailID": detail_id,
                    "purposeID": self._esb_purpose_for(line),
                    # THE signed variance. Never line.counted_qty.
                    "qty": line.variance_qty,
                    "hpp": self._esb_unit_cost(line),
                }
            )
        return {
            "itemJournalDate": fields.Date.to_string(self.completed_at or fields.Date.context_today(self)),
            "branchID": self.esb_branch_id.esb_branch_id,
            "locationID": self.esb_location_id.esb_location_id,
            "requestTemplateID": None,
            "additionalInfo": _("Odoo stock opname %s") % self.name,
            "itemJournalDetails": details,
        }

    def _esb_purpose_for(self, line):
        """Purpose drives the GL account of the variance on ESB's side."""
        Purpose = self.env["custom.esb.purpose"].sudo()
        field = "is_default_gain" if line.variance_qty > 0 else "is_default_loss"
        purpose = Purpose.search([(field, "=", True)], limit=1)
        if not purpose:
            raise UserError(
                _(
                    "No default ESB purpose is flagged for stock %s. Set one under "
                    "ESB → Master Data → Purposes — it determines which account the "
                    "variance posts to in ESB's general ledger."
                )
                % (_("gains") if line.variance_qty > 0 else _("losses"))
            )
        return purpose.esb_purpose_id

    def _esb_unit_cost(self, line):
        """hpp = the unit value ESB last valued this stock at."""
        snap = (
            self.env["custom.esb.stock.snapshot"]
            .sudo()
            .search(
                [("location_id", "=", self.esb_location_id.id), ("product_id", "=", line.product_id.id)],
                limit=1,
            )
        )
        return snap.unit_value or line.product_id.standard_price or 0.0
