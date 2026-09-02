# -*- coding: utf-8 -*-
"""On-hand balances mirrored from ESB.

ESB exposes **no bulk stock-on-hand endpoint**. The only way to obtain balances
for a whole location is ``GET /report/stock-movement``, which returns individual
movements each carrying a running ``qtyBalance``. So the snapshot is built by
paging the report for a window and keeping the **last** row per
(branch, location, productDetailID) — that row's ``qtyBalance`` is the closing
balance at the end of the window.

Two consequences worth knowing:

- A product with **no movement in the window has no row**, and therefore no
  snapshot. Its balance is whatever it was before the window. Use a lookback
  long enough to cover slow movers (``esb.snapshot_lookback_days``, default 90)
  and treat a missing snapshot as "unknown", never as zero — booking an opname
  against an assumed zero would write a bogus adjustment into ESB's GL.
- Ordering matters. The report is not guaranteed sorted, so rows are ordered by
  ``(documentDate, createdDate)`` before the last one is taken.

``/product/stock-location`` gives an authoritative single-product balance and is
used by :meth:`refresh_one` to spot-verify a SKU before writing to ESB.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .esb_adapter import ESB_CORE, EsbApiError

_logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 90
SOURCES = [("movement_report", "Stock Movement Report"), ("stock_location", "Stock Location Lookup")]


class EsbStockSnapshot(models.Model):
    _name = "custom.esb.stock.snapshot"
    _description = "ESB Stock Snapshot"
    _order = "branch_id, location_id, product_id"
    _rec_name = "product_id"

    branch_id = fields.Many2one("custom.esb.branch", required=True, ondelete="cascade", index=True)
    location_id = fields.Many2one("custom.esb.location", ondelete="cascade", index=True)
    product_id = fields.Many2one("product.product", required=True, ondelete="cascade", index=True)
    esb_product_detail_id = fields.Integer(index=True)
    company_id = fields.Many2one(related="branch_id.company_id", store=True, index=True)

    qty = fields.Float(digits=(20, 4), help="Closing qtyBalance in the reported unit.")
    value = fields.Float(digits=(20, 4), help="Closing amountBalance.")
    unit_value = fields.Float(digits=(20, 4), help="valuePerUnit — used as hpp on item journals.")
    uom_name = fields.Char()
    as_of = fields.Datetime(index=True, help="End of the window this balance was derived from.")
    last_movement_date = fields.Date(help="Date of the movement this balance came from.")
    source = fields.Selection(SOURCES, default="movement_report")

    _branch_loc_product_uniq = models.Constraint(
        "unique(branch_id, location_id, product_id)",
        "Only one snapshot row per branch/location/product.",
    )

    @api.depends("product_id", "branch_id", "location_id")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = (
                f"{rec.product_id.display_name} @ {rec.location_id.display_name or rec.branch_id.display_name}"
            )

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    @api.model
    def _cron_refresh_snapshots(self):
        sync = self.env["custom.esb.master.sync"]
        log = self.env["custom.esb.sync.log"]
        if not sync._enabled("esb.snapshot_enabled"):
            log._record("pull", "snapshot", "skipped", message="esb.snapshot_enabled is off")
            return False
        branches = self.env["custom.esb.branch"].sudo().search(self._branch_domain())
        if not branches:
            log._record("pull", "snapshot", "skipped", message="No active ESB branches — run the master sync first")
            return False
        for branch in branches:
            self.refresh_branch(branch)
        return True

    @api.model
    def _branch_domain(self):
        """Active branches, optionally narrowed by ``esb.branch_whitelist``."""
        domain = [("active", "=", True)]
        raw = self.env["ir.config_parameter"].sudo().get_param("esb.branch_whitelist", "") or ""
        codes = [c.strip() for c in raw.split(",") if c.strip()]
        if codes:
            domain.append(("code", "in", codes))
        return domain

    @api.model
    def _lookback_days(self):
        raw = self.env["ir.config_parameter"].sudo().get_param("esb.snapshot_lookback_days", "")
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return DEFAULT_LOOKBACK_DAYS

    @api.model
    def refresh_branch(self, branch, date_to=None, lookback_days=None):
        """Rebuild every snapshot row for one branch from the movement report."""
        sync = self.env["custom.esb.master.sync"]
        log = self.env["custom.esb.sync.log"]
        adapter = sync._adapter(ESB_CORE)
        if adapter is None:
            log._record("pull", "snapshot:%s" % branch.code, "skipped", message="No active esb_core adapter config")
            return False
        date_to = date_to or fields.Date.context_today(self)
        days = lookback_days or self._lookback_days()
        date_from = date_to - timedelta(days=days)
        t0 = time.time()
        try:
            rows = list(
                adapter.iter_rows(
                    "report/stock-movement",
                    {
                        "startPeriod": fields.Date.to_string(date_from),
                        "endPeriod": fields.Date.to_string(date_to),
                        "branchCode": branch.code,
                        "unitToShow": "Default Stock Unit",
                    },
                )
            )
        except EsbApiError as exc:
            log._record("pull", "snapshot:%s" % branch.code, "error", message=str(exc))
            return False
        stats = self._apply_movement_rows(branch, rows, as_of=fields.Datetime.now())
        log._record(
            "pull",
            "snapshot:%s" % branch.code,
            "ok",
            record_count=len(rows),
            duration_ms=int((time.time() - t0) * 1000),
            **stats,
        )
        return True

    @api.model
    def _apply_movement_rows(self, branch, rows, as_of):
        """Reduce movement rows to closing balances and upsert them.

        Split out from the HTTP fetch so it can be unit-tested against fixtures
        without a transport.
        """
        locations = {loc.esb_location_id: loc for loc in branch.location_ids}
        by_name = {(loc.name or "").strip().lower(): loc for loc in branch.location_ids}
        products = {}
        latest = {}
        for row in rows:
            detail_id = row.get("productDetailID")
            if not detail_id:
                continue
            loc = self._resolve_location(row, locations, by_name)
            key = (loc.id if loc else 0, detail_id)
            # Keep the chronologically last movement; the report is not sorted.
            sort_key = (row.get("documentDate") or "", row.get("createdDate") or "")
            if key not in latest or sort_key >= latest[key][0]:
                latest[key] = (sort_key, row, loc)

        created = updated = 0
        Detail = self.env["custom.esb.product.detail"].sudo()
        for (loc_id, detail_id), (_sort, row, _loc) in latest.items():
            if detail_id not in products:
                detail = Detail.search([("esb_product_detail_id", "=", detail_id)], limit=1)
                products[detail_id] = detail.product_id
            product = products[detail_id]
            if not product:
                # Product not mirrored yet — skip rather than invent one. The
                # next master sync will pick it up.
                continue
            vals = {
                "branch_id": branch.id,
                "location_id": loc_id or False,
                "product_id": product.id,
                "esb_product_detail_id": detail_id,
                "qty": row.get("qtyBalance") or 0.0,
                "value": row.get("amountBalance") or 0.0,
                "unit_value": row.get("valuePerUnit") or 0.0,
                "uom_name": row.get("UOM"),
                "as_of": as_of,
                "last_movement_date": row.get("documentDate") or False,
                "source": "movement_report",
            }
            existing = self.sudo().search(
                [
                    ("branch_id", "=", branch.id),
                    ("location_id", "=", loc_id or False),
                    ("product_id", "=", product.id),
                ],
                limit=1,
            )
            if existing:
                existing.write(vals)
                updated += 1
            else:
                self.sudo().create(vals)
                created += 1
        return {"created_count": created, "updated_count": updated}

    @staticmethod
    def _resolve_location(row, by_id, by_name):
        """The movement report identifies a location by *name*, not ID."""
        name = (row.get("location") or "").strip().lower()
        if name and name in by_name:
            return by_name[name]
        loc_id = row.get("locationID")
        if loc_id and loc_id in by_id:
            return by_id[loc_id]
        return None

    # ------------------------------------------------------------------
    # Single-product verification
    # ------------------------------------------------------------------

    @api.model
    def refresh_one(self, location, product):
        """Authoritative single-SKU balance via ``/product/stock-location``.

        Use before writing an adjustment: the movement-report snapshot can be
        stale by a whole cron interval, and an opname delta computed against a
        stale expected quantity posts the wrong number into ESB's GL.
        """
        sync = self.env["custom.esb.master.sync"]
        adapter = sync._adapter(ESB_CORE, raise_if_missing=True)
        detail_id = product._esb_detail_id("stock")
        if not detail_id:
            raise UserError(_("Product %s has no ESB product detail ID.") % product.display_name)
        rows = adapter.get_rows(
            "product/stock-location",
            {"productDetailID": detail_id, "locationID": location.esb_location_id},
        )
        if not rows:
            return self.browse()
        row = rows[0]
        qty = row.get("stockQty")
        if qty is None:
            qty = row.get("qty") or 0.0
        vals = {
            "branch_id": location.branch_id.id,
            "location_id": location.id,
            "product_id": product.id,
            "esb_product_detail_id": detail_id,
            "qty": qty,
            "uom_name": row.get("uomName"),
            "as_of": fields.Datetime.now(),
            "source": "stock_location",
        }
        existing = self.sudo().search(
            [
                ("branch_id", "=", location.branch_id.id),
                ("location_id", "=", location.id),
                ("product_id", "=", product.id),
            ],
            limit=1,
        )
        if existing:
            existing.write(vals)
            return existing
        return self.sudo().create(vals)

    @api.model
    def _stale_before(self):
        """Cut-off datetime beyond which a snapshot should not be trusted.

        Anything older has had time to drift: ESB keeps transacting between our
        cron runs. Consumers use this to warn before computing a variance
        against a balance that may already be wrong.
        """
        raw = self.env["ir.config_parameter"].sudo().get_param("esb.snapshot_stale_hours", "")
        try:
            hours = max(1, int(raw))
        except (TypeError, ValueError):
            hours = 24
        return fields.Datetime.now() - timedelta(hours=hours)

    @api.model
    def qty_for(self, location, product):
        """Snapshot quantity, or ``None`` when unknown.

        ``None`` is meaningful and must not be coerced to ``0.0`` by callers —
        see the module docstring.
        """
        snap = self.sudo().search(
            [("location_id", "=", location.id), ("product_id", "=", product.id)],
            limit=1,
        )
        return snap.qty if snap else None
