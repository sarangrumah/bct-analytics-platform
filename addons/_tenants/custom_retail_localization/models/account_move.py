# -*- coding: utf-8 -*-
"""Journal Billing printout helpers on ``account.move``.

The Journal Billing document renders a posted vendor bill as a GL voucher:
a two-column meta header (Reference / Invoice AP / dates / amount-in-words on
the left; Vendor / PO / Tax on the right) and a GL table of every journal item
(GL account, description, operating unit, debit, credit).

All lookups that depend on optional modules (l10n_id tax number, analytic
"operating unit", linked PO) are resolved defensively here so the QWeb stays
declarative and never crashes on a DB where a field is absent.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

from .terbilang import terbilang_id

BILL_TRADE_SEQ = "account.move.retail.bill.trade"
BILL_NONTRADE_SEQ = "account.move.retail.bill.nontrade"


class AccountMove(models.Model):
    _inherit = "account.move"

    # Trade / Non-Trade stream, inherited from the source PO (feature #9). Drives
    # the payable account routing done in ``account.move.line._compute_account_id``.
    l10n_purchase_type = fields.Selection(
        [("trade", "Trade"), ("non_trade", "Non-Trade")],
        string="Purchase Type",
        copy=False,
        help="Trade / Non-Trade stream carried from the purchase order, or picked "
        "directly on a manually-created bill. Selects the payable account and the "
        "BILL/T vs BILL/NT numbering on this bill.",
    )

    # Operating Unit (feature #9) — header convenience that cascades to the
    # product lines. Each line carries its own l10n_ou_analytic_id (the source of
    # truth for the analytic distribution); this header field just fills the
    # lines in one click on a manual bill. No default so PO-derived bills (whose
    # lines already carry the store OU) are left untouched.
    l10n_ou_analytic_id = fields.Many2one(
        "account.analytic.account",
        string="Operating Unit",
        copy=False,
        domain="[('plan_id.name', '=', 'Operating Unit')]",
        help="Head Office / Store this bill belongs to. Sets the Operating Unit "
        "on every product line for per-OU P&L reporting.",
    )

    @api.onchange("l10n_ou_analytic_id")
    def _onchange_l10n_ou_analytic_id(self):
        """Cascade the header OU onto product lines that have none yet."""
        if not self.l10n_ou_analytic_id:
            return
        for line in self.invoice_line_ids:
            if line.display_type == "product" and not line.l10n_ou_analytic_id:
                line.l10n_ou_analytic_id = self.l10n_ou_analytic_id

    # ------------------------------------------------------------------
    # Trade / Non-Trade vendor-bill numbering (Finance-AP #8 / Accounting #16)
    # ------------------------------------------------------------------
    # Vendor bills carry an l10n_purchase_type and post to the store's shared
    # purchase journal. To keep Trade and Non-Trade on separate, monthly-reset
    # counters (BILL/EBR/... vs BILL/NT/EBR/...) we bypass the sequence.mixin and
    # draw from our own ir.sequence, exactly like the PO numbering. On DBs where
    # the tenant sequences are absent, or for moves without a purchase type
    # (customer invoices, manual entries), core numbering is used unchanged.
    def _retail_wants_bill_number(self):
        self.ensure_one()
        return self.move_type in ("in_invoice", "in_refund") and bool(self.l10n_purchase_type)

    def _retail_effective_date(self):
        self.ensure_one()
        dt = self.date or self.invoice_date or fields.Date.today()
        return dt.date() if isinstance(dt, datetime) else dt

    @staticmethod
    def _retail_draw_monthly(seq, dt):
        """Draw the next number from ``seq`` with a per-month reset.

        Native ir.sequence date ranges are yearly, so ensure a monthly
        ``ir.sequence.date_range`` covering ``dt`` before drawing the counter.
        """
        DateRange = seq.env["ir.sequence.date_range"].sudo()
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

    def _retail_next_bill_number(self):
        """Next monthly-reset bill number for this move's purchase type.

        Mirrors ``purchase.order._retail_next_po_number``. Returns ``False`` when
        the tenant sequence is absent (non-the apparel brand DBs keep core numbering).
        """
        self.ensure_one()
        code = BILL_TRADE_SEQ if self.l10n_purchase_type == "trade" else BILL_NONTRADE_SEQ
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
        return self._retail_draw_monthly(seq, self._retail_effective_date())

    # ------------------------------------------------------------------
    # Payment numbering (Finance-AP #9): <last-4 of bank account>/YYYY/MM/###
    # ------------------------------------------------------------------
    # A payment's journal entry (origin_payment_id set) that posts through a
    # the apparel brand bank journal — i.e. a bank journal that carries a bank account —
    # is numbered per rekening: the last 4 digits of the account number, a
    # monthly-reset 3-digit counter, one independent sequence per journal.
    # Bank journals without a bank account (e.g. the generic BNK1, or non-the apparel brand
    # DBs) keep core numbering.
    def _retail_wants_payment_number(self):
        self.ensure_one()
        journal = self.journal_id
        return bool(
            self.origin_payment_id
            and journal.type == "bank"
            and journal.bank_account_id
            and journal.bank_account_id.acc_number
        )

    def _retail_next_payment_number(self):
        self.ensure_one()
        acc_number = self.journal_id.bank_account_id.acc_number or ""
        digits = re.sub(r"\D", "", acc_number)
        if len(digits) < 4:
            return False
        last4 = digits[-4:]
        company = self.company_id or self.env.company
        code = "account.payment.retail.j%s" % self.journal_id.id
        Seq = self.env["ir.sequence"].sudo()
        seq = Seq.search(
            [("code", "=", code), ("company_id", "in", [company.id, False])],
            order="company_id",
            limit=1,
        )
        if not seq:
            seq = Seq.create(
                {
                    "name": "the apparel brand Payment %s (%s)" % (last4, self.journal_id.code or ""),
                    "code": code,
                    "prefix": "%s/%%(year)s/%%(month)s/" % last4,
                    "padding": 3,
                    "use_date_range": True,
                    "implementation": "no_gap",
                    "company_id": company.id,
                }
            )
        return self._retail_draw_monthly(seq, self._retail_effective_date())

    # Re-declaring @api.depends REPLACES the inherited set, so mirror core's
    # dependencies exactly (see account.move._compute_name) plus l10n_purchase_type.
    @api.depends(
        "posted_before",
        "state",
        "journal_id",
        "date",
        "move_type",
        "origin_payment_id",
        "l10n_purchase_type",
    )
    def _compute_name(self):
        # Assign the apparel brand number (Trade/Non-Trade bill, or per-rekening payment)
        # for qualifying moves leaving draft without a name yet, then exclude them
        # from core so the sequence.mixin (which would otherwise re-detect the
        # format or reset the name to match the date) never touches them.
        numbered = self.browse()
        for move in self:
            if not (move.state != "draft" and (not move.name or move.name == "/") and (move.date or move.invoice_date)):
                continue
            number = False
            if move._retail_wants_bill_number():
                number = move._retail_next_bill_number()
            elif move._retail_wants_payment_number():
                number = move._retail_next_payment_number()
            if number:
                move.name = number
                numbered |= move
        super(AccountMove, self - numbered)._compute_name()

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------
    def _edo_first_field(self, *names, default=""):
        """Return the first populated value among ``names`` that actually
        exists on this model — tolerant of optional l10n modules."""
        self.ensure_one()
        for name in names:
            if name in self._fields and self[name]:
                return self[name]
        return default

    def _edo_gl_lines(self):
        """Journal items shown in the GL table (excludes section/note rows).

        Ordered to mirror the vendor-bill screen so Accounting can review the
        print-out against the form: product/invoice lines first (in their own
        sequence), then tax lines, then the payment-term / payable line.
        """
        self.ensure_one()
        lines = self.line_ids.filtered(lambda l: l.display_type not in ("line_section", "line_note"))
        rank = {"product": 0, "tax": 1, "payment_term": 2}
        return lines.sorted(key=lambda l: (rank.get(l.display_type, 3), l.sequence, l.id))

    # ------------------------------------------------------------------
    # Header meta
    # ------------------------------------------------------------------
    def _edo_po_number(self):
        """PO reference: linked purchase order(s), else the bill origin."""
        self.ensure_one()
        if "purchase_line_id" in self.line_ids._fields:
            names = self.line_ids.mapped("purchase_line_id.order_id.name")
            names = sorted({n for n in names if n})
            if names:
                return ", ".join(names)
        return self.invoice_origin or ""

    def _edo_tax_number(self):
        """Faktur Pajak / tax invoice number.

        the apparel brand carries this in ``x_custom_nsfp`` (No. Faktur Pajak / NSFP, from
        ``custom_coretax``); the l10n_id names are kept as fallbacks so the
        report still works on a DB without Coretax.
        """
        return self._edo_first_field("x_custom_nsfp", "l10n_id_tax_number", "l10n_id_kode_transaksi", default="")

    def _edo_tax_date(self):
        """Tax point date — Coretax "Tanggal Faktur Pajak" (``x_custom_tanggal_
        faktur_pajak``), then any l10n_id tax date, finally the invoice date."""
        self.ensure_one()
        return (
            self._edo_first_field("x_custom_tanggal_faktur_pajak", "l10n_id_tax_date", default=False)
            or self.invoice_date
        )

    def _edo_exchange_rate(self):
        """Rate of the bill currency against the company currency (1.0 when equal)."""
        self.ensure_one()
        if self.currency_id == self.company_id.currency_id:
            return 1.0
        if self.amount_total:
            return abs(self.amount_total_signed) / self.amount_total
        return 1.0

    def _edo_bill_warehouse(self):
        """Warehouse(s)/store(s) of the purchase order(s) behind this bill."""
        self.ensure_one()
        if "purchase_line_id" not in self.line_ids._fields:
            return self.env["stock.warehouse"]
        orders = self.line_ids.mapped("purchase_line_id.order_id")
        return orders.mapped("picking_type_id.warehouse_id")

    def _edo_line_ou_analytic(self, line):
        """Operating-Unit analytic account carried by ``line``, if any.

        Checks the explicit ``l10n_ou_analytic_id`` pick first, then the
        OU-plan account stamped inside ``analytic_distribution`` (PO-derived
        lines get their OU only through the distribution).
        """
        ou = line.l10n_ou_analytic_id if "l10n_ou_analytic_id" in line._fields else False
        if ou:
            return ou
        for key in line.analytic_distribution or {}:
            for acc_id in str(key).split(","):
                acc = self.env["account.analytic.account"].browse(int(acc_id)).exists()
                if acc and acc.plan_id.name == "Operating Unit":
                    return acc
        return self.env["account.analytic.account"]

    def _edo_operating_unit(self, line):
        """ "Operating Unit" = the store/OU of the source document.

        Priority: the line's own OU analytic (manual pick or the OU stamped in
        its analytic distribution), then the OU of any product line on the bill
        (so tax/payable rows show the store too), then the PO warehouse, and
        finally the company name when the bill has no OU/purchase origin at all.
        """
        self.ensure_one()
        ou = self._edo_line_ou_analytic(line)
        if not ou:
            for other in self.line_ids.filtered(lambda l: l.display_type == "product"):
                ou = self._edo_line_ou_analytic(other)
                if ou:
                    break
        if ou:
            return ou.name
        pol = line.purchase_line_id if "purchase_line_id" in line._fields else False
        wh = pol.order_id.picking_type_id.warehouse_id if pol else False
        if not wh:
            wh = self._edo_bill_warehouse()[:1]
        if wh:
            return wh.name
        return self.company_id.name or ""

    # ------------------------------------------------------------------
    # Amount in words
    # ------------------------------------------------------------------
    def _edo_amount_in_words(self):
        self.ensure_one()
        suffix = "Rupiah" if self.currency_id.name == "IDR" else (self.currency_id.currency_unit_label or "")
        return terbilang_id(self.amount_total, suffix=suffix)
