# -*- coding: utf-8 -*-
import logging
from collections import defaultdict
from datetime import datetime, time, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PpobRollup(models.AbstractModel):
    _name = "custom.ppob.rollup"
    _description = "PPOB Daily Rollup Engine"

    @api.model
    def _cron_daily_rollup(self, rollup_date=None):
        """Aggregate yesterday's (or given) success transactions per mitra into a
        ``sale.order`` + confirmed summary ``account.move`` (``out_invoice``).

        Idempotent: transactions already assigned ``x_custom_ppob_rollup_so_id``
        are skipped; re-running does not double-produce.
        """
        if rollup_date is None:
            rollup_date = fields.Date.context_today(self) - timedelta(days=1)
        if isinstance(rollup_date, str):
            rollup_date = fields.Date.from_string(rollup_date)

        Txn = self.env["custom.ppob.transaction"]
        day_start = datetime.combine(rollup_date, time(0, 0, 0))
        day_end = datetime.combine(rollup_date + timedelta(days=1), time(0, 0, 0))
        pending = Txn.search(
            [
                ("state", "=", "success"),
                ("x_custom_ppob_rollup_so_id", "=", False),
                ("completed_at", ">=", day_start),
                ("completed_at", "<", day_end),
            ]
        )
        if not pending:
            _logger.info("PPOB rollup %s: nothing to do", rollup_date)
            return 0

        grouped = defaultdict(lambda: self.env["custom.ppob.transaction"])
        for t in pending:
            grouped[t.mitra_id.id] |= t

        created = 0
        for mitra_id, txns in grouped.items():
            so = self._create_rollup_so(self.env["res.partner"].browse(mitra_id), txns, rollup_date)
            txns.write({"x_custom_ppob_rollup_so_id": so.id})
            try:
                self._invoice_rollup(so)
            except Exception:
                _logger.exception("Failed to invoice rollup SO %s", so.name)
            created += 1
        return created

    def _create_rollup_so(self, mitra, txns, rollup_date):
        """Build one sale.order grouping txns by (product_id, sell_price)."""
        SaleOrder = self.env["sale.order"]
        lines_by_key = defaultdict(lambda: {"qty": 0.0, "price": 0.0, "product": None})
        for t in txns:
            key = (t.product_id.id, t.sell_price)
            lines_by_key[key]["qty"] += 1
            lines_by_key[key]["price"] = t.sell_price
            lines_by_key[key]["product"] = t.product_id
        order_line = []
        for (_pid, _price), agg in lines_by_key.items():
            odoo_product = self._ensure_odoo_product(agg["product"])
            order_line.append(
                (
                    0,
                    0,
                    {
                        "product_id": odoo_product.id,
                        "name": f"{agg['product'].display_name} (PPOB rollup {rollup_date})",
                        "product_uom_qty": agg["qty"],
                        "price_unit": agg["price"],
                    },
                )
            )
        so = SaleOrder.create(
            {
                "partner_id": mitra.id,
                "x_custom_ppob_is_rollup": True,
                "x_custom_ppob_rollup_date": rollup_date,
                "date_order": datetime.combine(rollup_date, time(23, 59, 0)),
                "order_line": order_line,
                "client_order_ref": f"PPOB Rollup {mitra.x_custom_ppob_mitra_code or mitra.display_name} {rollup_date}",
            }
        )
        so.action_confirm()
        return so

    def _ensure_odoo_product(self, ppob_product):
        """Lazily provision a matching ``product.product`` so the sale.order line
        validates. Service-type (no stock), invoice_policy=order so the ordered
        qty is immediately invoiceable, reused across mitra."""
        Product = self.env["product.product"]
        existing = Product.search([("default_code", "=", ppob_product.code)], limit=1)
        if existing:
            return existing
        return Product.create(
            {
                "name": ppob_product.name,
                "default_code": ppob_product.code,
                "type": "service",
                "invoice_policy": "order",
                "list_price": ppob_product.denom or 0.0,
                "standard_price": ppob_product.cost_price_default or 0.0,
            }
        )

    def _invoice_rollup(self, so):
        """Create + post the summary invoice on the report-excluded summary
        journal. Revenue is already booked per transaction, so this document is
        for e-Faktur / Coretax only (its journal is omitted from the TB)."""
        if not so.order_line:
            return False
        journal = self.env.ref("custom_ppob_rollup.journal_ppob_summary", raise_if_not_found=False)
        if not journal:
            raise UserError(_("PPOB Summary journal not configured."))
        moves = so._create_invoices()
        if not moves:
            return False
        moves.write(
            {
                "x_custom_ppob_is_summary": True,
                "journal_id": journal.id,
            }
        )
        moves.action_post()
        return moves
