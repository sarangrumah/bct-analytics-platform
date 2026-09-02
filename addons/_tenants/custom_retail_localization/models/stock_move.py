# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class StockMove(models.Model):
    _inherit = "stock.move"

    @api.constrains("purchase_line_id", "picking_id")
    def _check_retail_receipt_line_from_po(self):
        # Requirement: on a PO-linked goods receipt only lines that originate
        # from the purchase order (i.e. carry a purchase_line_id) may be
        # received. Any manually added line -- even for a product that is
        # already on the PO -- is rejected the moment it is saved.
        for move in self:
            picking = move.picking_id
            if not picking or picking.picking_type_code != "incoming":
                continue
            if move.purchase_line_id:
                # Line originates from a PO line -> allowed.
                continue
            orders = picking._retail_purchase_orders()
            if not orders:
                # Receipt not tied to any purchase order -> no restriction.
                continue
            raise ValidationError(
                _(
                    "Line '%(product)s' was not created from purchase order "
                    "%(orders)s and cannot be added to this receipt.\n"
                    "Only lines that come from the purchase order may be "
                    "received here.",
                    product=move.product_id.display_name,
                    orders=", ".join(orders.mapped("name")) or "-",
                )
            )

    def _is_retail_goods_receipt(self):
        """A vendor goods receipt: stock entering from a supplier location.

        This deliberately excludes internal transfers, manufacturing receipts
        and customer returns — only true vendor receipts skip GL posting.
        """
        self.ensure_one()
        return self.location_id.usage == "supplier"

    def _is_retail_vendor_return(self):
        """A vendor return (RTV): stock leaving the company back to a supplier.

        The mirror of a goods receipt — inventory decreases and the value flows
        out to the vendor. Custom/customer returns and internal transfers are
        excluded (destination must be a supplier location).
        """
        self.ensure_one()
        return self.location_dest_id.usage == "supplier"

    # Config switch (ir.config_parameter) that decides whether vendor goods
    # receipts skip the GL posting. Default OFF -> Automated valuation posts the
    # GR journal natively (Dr Inventory / Cr Stock-Input/GRIR). Set to "1" to
    # restore the periodic behaviour (no GR journal + Inventory Reconciliation).
    _SUPPRESS_GR_PARAM = "custom_retail_localization.suppress_gr_journal"

    def _retail_suppress_gr_journal(self):
        param = self.env["ir.config_parameter"].sudo().get_param(self._SUPPRESS_GR_PARAM, "0")
        return str(param).strip().lower() not in ("0", "false", "", "none")

    def _should_create_account_move(self):
        # Requirement 3 (now opt-in): suppress the inventory journal on Goods
        # Receipt confirm ONLY when the periodic switch is enabled. When it is
        # off (default), Automated valuation posts the GR journal normally; the
        # stock valuation layer is produced by stock_account either way.
        self.ensure_one()
        if self._retail_suppress_gr_journal() and self._is_retail_goods_receipt():
            return False
        return super()._should_create_account_move()

    # ------------------------------------------------------------------
    # GR journal — posted the platform way (valuation + stock-variation)
    # ------------------------------------------------------------------
    # This build has NO standard stock input/output interim account fields, so
    # core real-time valuation posts nothing on a receipt. Inventory GL is wired
    # only through property_stock_valuation_account_id + account_stock_variation_id
    # (the same pair the Inventory Reconciliation tool books). So when the GR
    # journal is wanted (suppress switch OFF, the default), post it directly:
    #   Dr Stock Valuation account / Cr Stock Variation (GR/IR) account
    # for the received inventory value (net of recoverable tax = move.value).
    _GR_JOURNAL_REF = "GR-VAL:%s"  # goods receipt, per stock.move (idempotency)
    _RET_JOURNAL_REF = "GR-RET-VAL:%s"  # vendor return, per stock.move

    def _action_done(self, cancel_backorder=False):
        done_moves = super()._action_done(cancel_backorder=cancel_backorder)
        done_moves._retail_post_gr_journal()
        done_moves._retail_post_return_journal()
        return done_moves

    def _retail_post_gr_journal(self):
        for move in self:
            if move.state != "done" or not move._is_retail_goods_receipt():
                continue
            if move._retail_suppress_gr_journal():
                continue
            # Goods receipt: inventory increases.
            #   Dr Stock Valuation / Cr Stock Variation (GR/IR)
            label = _("Goods Receipt %(picking)s", picking=move.picking_id.name or "")
            move._retail_book_valuation_entry(
                ref=self._GR_JOURNAL_REF % move.id,
                label=label,
                incoming=True,
            )

    def _retail_post_return_journal(self):
        for move in self:
            if move.state != "done" or not move._is_retail_vendor_return():
                continue
            if move._retail_suppress_gr_journal():
                continue
            # Vendor return (RTV): inventory decreases — the exact reverse of a
            # goods receipt.  Dr Stock Variation (GR/IR) / Cr Stock Valuation
            label = _("Vendor Return %(picking)s", picking=move.picking_id.name or "")
            move._retail_book_valuation_entry(
                ref=self._RET_JOURNAL_REF % move.id,
                label=label,
                incoming=False,
            )

    def _retail_book_valuation_entry(self, ref, label, incoming):
        """Post the platform-way inventory entry for one done move.

        ``incoming`` True -> Dr Valuation / Cr Variation (goods receipt);
        ``incoming`` False -> Dr Variation / Cr Valuation (vendor return).
        No-op (idempotent) when already posted for ``ref``, when the category is
        not real-time, when the valuation/variation accounts or journal are
        missing, or when ``move.value`` is zero.
        """
        self.ensure_one()
        AccountMove = self.env["account.move"]
        company = self.company_id
        categ = self.product_id.categ_id.with_company(company)
        if categ.property_valuation != "real_time":
            return
        val_acc = categ.property_stock_valuation_account_id
        var_acc = categ.account_stock_variation_id
        # Trade / Non-Trade (feature #9): route the GR/IR clearing (stock
        # variation) account per purchase stream. The mapping supplies the
        # non-trade GR/IR account; trade keeps the product category's own
        # per-category GR/IR (mapping.grir_account_id left empty for trade).
        ptype = self.picking_id.l10n_purchase_type
        if ptype:
            mapping = self.env["retail.purchase.account.map"]._get_map(company, ptype)
            if mapping and mapping.grir_account_id:
                var_acc = mapping.grir_account_id
        journal = categ.property_stock_journal or company.account_stock_journal_id
        if not (val_acc and var_acc and journal):
            return
        # Operating-Unit analytic of the receiving store, stamped on both legs so
        # the GR journal is sliceable per store.
        ou = self.picking_id.picking_type_id.warehouse_id.l10n_ou_analytic_id
        analytic = {str(ou.id): 100.0} if ou else False
        # move.value is the inventory value moved (net of recoverable tax on a
        # receipt; cost of goods returned on an RTV). Always a magnitude here.
        amount = self.value
        currency = company.currency_id
        if not amount or currency.is_zero(amount):
            return
        if AccountMove.search_count([("ref", "=", ref), ("company_id", "=", company.id)]):
            return  # already posted for this move
        debit_acc, credit_acc = (val_acc, var_acc) if incoming else (var_acc, val_acc)
        AccountMove.create(
            {
                "move_type": "entry",
                "journal_id": journal.id,
                "company_id": company.id,
                "date": fields.Date.context_today(self),
                "ref": ref,
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "account_id": debit_acc.id,
                            "name": label,
                            "debit": amount if amount > 0 else 0.0,
                            "credit": -amount if amount < 0 else 0.0,
                            "analytic_distribution": analytic,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "account_id": credit_acc.id,
                            "name": label,
                            "debit": -amount if amount < 0 else 0.0,
                            "credit": amount if amount > 0 else 0.0,
                            "analytic_distribution": analytic,
                        },
                    ),
                ],
            }
        ).action_post()
