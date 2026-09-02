# -*- coding: utf-8 -*-
"""Book the source workbook's discount inside the store's own POS closing entry.

X24DN carries a ``NET DISCOUNT AMOUNT`` per line. Booking it as a separate journal
entry produced one summary move per import (later: per day) that no store could tie to
its own sales. Finance reads the discount next to the revenue it reduced, so the
reclass belongs *in* the closing entry of the session that sold the goods:

    Dr POS Suspense Clearing          (core)
       Cr Gross Sales-<cat>           (core, net of discount)
       Cr VAT Out                     (core)
    Dr Sales Discount-<cat>           (here)
       Cr Gross Sales-<cat>           (here — grosses revenue back up)

Both added legs carry the file's figure verbatim, so the entry stays balanced and adds
no rounding selisih. A session is one store on one trading day, so the resulting lines
are automatically per store, per date, and carry that store's Operating Unit.

Gated by ``retail_import.x24_discount_reclass`` (mutually exclusive with
``retail_import.x31_post_enabled``). A live POS session has no ``ri_src_discount`` on any
line, so this is a no-op outside the importer.
"""

import logging
from collections import defaultdict

from odoo import models

_logger = logging.getLogger(__name__)


class PosSession(models.Model):
    _inherit = "pos.session"

    def _ri_discount_reclass_line_vals(self):
        """account.move.line vals grossing this session's revenue back up by its discount.

        Grouped per ``(income account, discount account, promo code, promo description)``
        so the entry names the promotion that granted each discount.
        """
        self.ensure_one()
        Executor = self.env["retail.import.executor"]
        if not Executor._x24_discount_reclass_enabled():
            return []
        lines = self.order_ids.lines.filtered("ri_src_discount")
        if not lines:
            return []

        company = self.company_id
        ou = Executor._ri_config_ou(self.config_id)
        acct_cache, by_key = {}, defaultdict(float)
        unmapped = 0.0
        for line in lines:
            product = line.product_id
            if product.id not in acct_cache:
                income = Executor._ri_income_account(company, product)
                discount = Executor._ri_category_account(company, product, "discount")
                acct_cache[product.id] = (income, discount) if (income and discount) else None
            pair = acct_cache[product.id]
            if not pair:
                unmapped += line.ri_src_discount
                continue
            income, discount = pair
            by_key[(income.id, discount.id, line.ri_discount_code or "", line.ri_discount_description or "")] += (
                line.ri_src_discount
            )

        if unmapped:
            _logger.warning(
                "retail import: session %s (%s) has %s of discount on products with no "
                "income/discount account; NOT reclassified",
                self.id,
                self.config_id.name,
                unmapped,
            )

        vals = []
        for (income_id, discount_id, code, description), amount in by_key.items():
            amount = round(amount, 2)
            if not amount:
                continue
            suffix = " — %s" % " ".join(filter(None, (code, description)))
            # An exchange can net to a negative discount for a category; flip the legs
            # rather than writing a negative debit.
            debit_account, credit_account = discount_id, income_id
            if amount < 0:
                debit_account, credit_account = income_id, discount_id
                amount = -amount
            # A fresh vals dict per line: ``analytic_distribution`` is a mutable Json
            # value and must never be shared between two create commands.
            vals.append(
                dict(
                    Executor._ri_ou_line_vals(ou),
                    move_id=self.move_id.id,
                    account_id=debit_account,
                    debit=amount,
                    credit=0.0,
                    name=("POS discount%s" % suffix)[:200],
                )
            )
            vals.append(
                dict(
                    Executor._ri_ou_line_vals(ou),
                    move_id=self.move_id.id,
                    account_id=credit_account,
                    debit=0.0,
                    credit=amount,
                    name=("POS discount gross-up%s" % suffix)[:200],
                )
            )
        return vals

    def _create_account_move(self, *args, **kwargs):
        # Runs while ``move_id`` is still draft (``_validate_session`` posts it right
        # after), and under ``check_move_validity=False``, so extra balanced pairs can be
        # appended here. ``_check_balanced`` runs afterwards and still sees a balanced move.
        data = super()._create_account_move(*args, **kwargs)
        vals = self._ri_discount_reclass_line_vals()
        if vals:
            self.env["account.move.line"].with_context(check_move_validity=False).create(vals)
            _logger.info(
                "retail import: session %s discount reclass -> %s line(s) in %s",
                self.id,
                len(vals),
                self.move_id.name or self.move_id.id,
            )
        return data
