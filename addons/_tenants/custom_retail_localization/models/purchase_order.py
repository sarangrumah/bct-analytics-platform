# -*- coding: utf-8 -*-
"""Feature #9 — Trade / Non-Trade purchases + Operating-Unit dimension.

On ``purchase.order``:

* ``l10n_purchase_type`` (trade / non-trade) drives a dedicated numbering
  sequence (``PO/T/EBR/YYYY/MM/#####`` vs ``PO/NT/EBR/YYYY/MM/#####``, monthly
  reset) and the account routing done downstream (payable + GR/IR).
* the vendor bill is routed to the store's own purchase journal and inherits the
  purchase type.

On ``purchase.order.line`` the store's Operating-Unit analytic account is merged
into ``analytic_distribution`` so it flows to the bill lines (core
``_related_analytic_distribution``) and the stock/anglo entries.
"""

from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

TRADE_SEQ = "purchase.order.retail.trade"
NONTRADE_SEQ = "purchase.order.retail.nontrade"


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    l10n_purchase_type = fields.Selection(
        [("trade", "Trade"), ("non_trade", "Non-Trade")],
        string="Purchase Type",
        default="trade",
        required=True,
        tracking=True,
        help="Trade = merchandise for resale (inventory, trade payable, trade "
        "GR/IR). Non-Trade = operational / opex (expense, non-trade payable, "
        "non-trade GR/IR). Drives the PO numbering and account mapping.",
    )

    l10n_ou_analytic_display = fields.Many2one(
        "account.analytic.account",
        string="Operating Unit",
        compute="_compute_l10n_ou_analytic_display",
        help="Operating-Unit analytic of the destination warehouse (Deliver To). "
        "This is what gets stamped on every PO line; pick the store's Receipt "
        "operation type in Deliver To to book the purchase on that store.",
    )

    # ------------------------------------------------------------------
    # Operating Unit (store) analytic
    # ------------------------------------------------------------------
    @api.depends("picking_type_id")
    def _compute_l10n_ou_analytic_display(self):
        for order in self:
            order.l10n_ou_analytic_display = order._retail_ou_analytic()

    def _retail_ou_analytic(self):
        """Operating-Unit analytic account of this PO's store, if any."""
        self.ensure_one()
        wh = self.picking_type_id.warehouse_id
        return wh.l10n_ou_analytic_id if wh else self.env["account.analytic.account"]

    # ------------------------------------------------------------------
    # Numbering: one monthly-reset sequence per purchase type
    # ------------------------------------------------------------------
    def _retail_next_po_number(self, purchase_type, seq_date=None):
        """Return the next PO number for ``purchase_type`` with a monthly reset.

        Native ``ir.sequence`` date ranges are yearly, so a monthly
        ``ir.sequence.date_range`` is ensured for the effective date before the
        counter is drawn — giving a per-month reset. Returns ``False`` when the
        tenant sequences are absent (non-the apparel brand DBs keep core numbering).
        """
        code = TRADE_SEQ if purchase_type == "trade" else NONTRADE_SEQ
        company = self.company_id or self.env.company
        seq = (
            self.env["ir.sequence"]
            .sudo()
            .search(
                [("code", "=", code), ("company_id", "in", [company.id, False])],
                order="company_id",
                limit=1,
            )
        )
        if not seq:
            return False
        dt = seq_date or fields.Date.today()
        if isinstance(dt, datetime):
            dt = dt.date()
        DateRange = self.env["ir.sequence.date_range"].sudo()
        rng = DateRange.search(
            [
                ("sequence_id", "=", seq.id),
                ("date_from", "<=", dt),
                ("date_to", ">=", dt),
            ],
            limit=1,
        )
        if not rng:
            first = dt.replace(day=1)
            last = first + relativedelta(months=1) - timedelta(days=1)
            DateRange.create({"sequence_id": seq.id, "date_from": first, "date_to": last})
        return seq.next_by_id(sequence_date=dt)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) not in (False, "New", "/", _("New")):
                continue
            company_id = vals.get("company_id") or self.default_get(["company_id"]).get("company_id")
            ptype = vals.get("l10n_purchase_type") or "trade"
            seq_date = None
            if vals.get("date_order"):
                seq_date = fields.Datetime.context_timestamp(self, fields.Datetime.to_datetime(vals["date_order"]))
            number = self.with_company(company_id)._retail_next_po_number(ptype, seq_date)
            if number:
                vals["name"] = number
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Vendor bill: route to the store journal, carry the purchase type
    # ------------------------------------------------------------------
    def _prepare_invoice(self):
        vals = super()._prepare_invoice()
        vals["l10n_purchase_type"] = self.l10n_purchase_type
        wh = self.picking_type_id.warehouse_id
        if wh and wh.l10n_purchase_journal_id:
            vals["journal_id"] = wh.l10n_purchase_journal_id.id
        return vals


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    # Preserve the base dependencies (product_id, order_id.partner_id) and add
    # the warehouse so the OU analytic re-stamps if the store changes. Re-declaring
    # @api.depends REPLACES the inherited set, so all triggers are listed here.
    @api.depends("product_id", "order_id.partner_id", "order_id.picking_type_id")
    def _compute_analytic_distribution(self):
        super()._compute_analytic_distribution()
        for line in self:
            if line.display_type:
                continue
            ou = line.order_id._retail_ou_analytic()
            if not ou:
                continue
            line.analytic_distribution = line._retail_merge_ou_distribution(line.analytic_distribution, ou.id)

    @staticmethod
    def _retail_merge_ou_distribution(distribution, ou_id):
        """Merge the Operating-Unit analytic ``ou_id`` into ``distribution``.

        analytic_distribution keys are comma-joined analytic-account ids (one per
        plan) mapping to a percentage. The OU lives in its own plan, so it is
        appended to every existing key; an empty distribution becomes
        ``{str(ou_id): 100}``.
        """
        ou = str(ou_id)
        if not distribution:
            return {ou: 100.0}
        merged = {}
        for key, pct in distribution.items():
            ids = key.split(",")
            if ou not in ids:
                ids.append(ou)
            merged[",".join(ids)] = pct
        return merged
