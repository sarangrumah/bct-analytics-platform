# -*- coding: utf-8 -*-
"""Move a product to another product category and repair the GL that followed the
old one.

At the apparel brand a ``product.category`` is not a label, it is an account mapping: the
category (or the first ancestor that carries one) supplies
``property_account_income_categ_id``, the ``Sales Discount-<cat>`` /
``Sales Return-<cat>`` contra accounts that ``custom_retail_import`` resolves via
``retail.import.executor._ri_category_account``, plus the expense and stock
valuation accounts. Put a product in the wrong category and its turnover lands on
the wrong ``Gross Sales-<x>`` account.

Correcting ``categ_id`` afterwards fixes nothing that is already booked: the POS
closing entries are posted, and — verified on ``prd_retail_begbal`` — they carry
**no** ``product_id`` at all, so no amount in the GL can be traced back to a
product. The only surviving link is ``pos.order.line``.

So this model recomputes, from the source documents, what each product actually
caused to be posted under its old category, and books the mirror image of it
against the new category:

    Dr Gross Sales-<old>          Cr Gross Sales-<new>
    Cr Sales Discount-<old>       Dr Sales Discount-<new>
    Cr Sales Return-<old>         Dr Sales Return-<new>

per (posting date, Operating Unit), as a DRAFT ``account.move`` for the
accountant to post — the same contract as ``retail.cogs.run`` and
``retail.inventory.reconciliation``.

Closed periods
--------------
Unlike the one-off ``66_reclass_sales_structure.py`` script, this **never lifts
``fiscalyear_lock_date``**. A correction whose source date sits in a closed period
is booked on ``fallback_date`` (today, i.e. the current open month) instead, and
the original date is kept on the line so the trail stays readable. That is the
policy Finance asked for: *kalau periode sudah closed, perbaikan dilakukan di
bulan berjalan.*
"""

from collections import defaultdict
from datetime import datetime, time

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare, float_is_zero

# POS orders that represent a real, settled sale — same set as retail.cogs.run.
_SOLD_STATES = ("paid", "done", "invoiced")

# Which category property each reclass line moves, and whether the account
# normally carries a credit (revenue) or a debit (contra-revenue, cost, asset)
# balance. Only used for labelling; the arithmetic works off the signed balance.
_KINDS = [
    ("income", "Gross Sales"),
    ("discount", "Sales Discount"),
    ("return", "Sales Return"),
    ("expense", "COGS"),
    ("valuation", "Inventory"),
]


class RetailCategReclass(models.Model):
    _name = "retail.categ.reclass"
    _description = "Product Category Reclassification"
    _order = "id desc"

    _CLEARING_PARAM = "custom_retail_localization.categ_reclass_clearing_account_code"

    name = fields.Char(default="/", copy=False, readonly=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")

    product_tmpl_ids = fields.Many2many(
        "product.template",
        string="Products",
        required=True,
        help="Products whose category is wrong. Every variant of each template is "
        "taken into account when the postings are recomputed.",
    )
    new_categ_id = fields.Many2one(
        "product.category",
        string="Correct Category",
        required=True,
        help="Category the products should have been in. Its account mapping "
        "(walked up the parent chain) supplies the target accounts.",
    )

    date_from = fields.Date(
        help="Only postings from this date on are corrected. Leave empty to cover "
        "everything ever posted for these products."
    )
    date_to = fields.Date(help="Leave empty to cover everything up to today.")

    posting_policy = fields.Selection(
        [
            ("auto", "Original date when the period is open, current month when closed"),
            ("current", "Always book in the current month"),
        ],
        default="auto",
        required=True,
        string="Posting Date",
    )
    fallback_date = fields.Date(
        string="Current-Month Date",
        required=True,
        default=fields.Date.context_today,
        help="Date used for corrections whose source period is already closed.",
    )
    journal_id = fields.Many2one(
        "account.journal",
        string="Journal",
        required=True,
        domain="[('type', '=', 'general'), ('company_id', '=', company_id)]",
        default=lambda self: self._default_journal(),
    )

    line_ids = fields.One2many("retail.categ.reclass.line", "reclass_id", copy=False)
    product_ids = fields.One2many("retail.categ.reclass.product", "reclass_id", copy=False, string="Category Changes")
    move_ids = fields.One2many("account.move", "retail_categ_reclass_id", readonly=True, copy=False)
    move_count = fields.Integer(compute="_compute_move_count")

    state = fields.Selection(
        [("draft", "Draft"), ("computed", "Computed"), ("applied", "Applied"), ("cancel", "Cancelled")],
        default="draft",
        copy=False,
    )

    total_amount = fields.Monetary(
        compute="_compute_totals",
        currency_field="currency_id",
        string="Reclassified Revenue",
        help="Turnover moved between Gross Sales accounts (the income lines only).",
    )
    closed_period_count = fields.Integer(compute="_compute_totals")
    unmatched_count = fields.Integer(compute="_compute_totals")
    warning_text = fields.Text(readonly=True, copy=False)

    def _default_journal(self):
        Journal = self.env["account.journal"]
        company = self.env.company
        return Journal.search([("code", "=", "GLJV"), ("company_id", "=", company.id)], limit=1) or Journal.search(
            [("type", "=", "general"), ("company_id", "=", company.id)], limit=1
        )

    @api.depends("line_ids.amount", "line_ids.kind", "line_ids.is_period_closed", "line_ids.is_matched")
    def _compute_totals(self):
        for rec in self:
            income = rec.line_ids.filtered(lambda line: line.kind == "income")
            rec.total_amount = -sum(income.mapped("amount"))
            rec.closed_period_count = len(rec.line_ids.filtered("is_period_closed"))
            rec.unmatched_count = len(rec.line_ids.filtered(lambda line: not line.is_matched))

    def _compute_move_count(self):
        for rec in self:
            rec.move_count = len(rec.move_ids)

    @api.constrains("date_from", "date_to")
    def _check_period(self):
        for rec in self:
            if rec.date_from and rec.date_to and rec.date_to < rec.date_from:
                raise UserError(_("The end date precedes the start date."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("retail.categ.reclass") or "/"
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Period policy
    # ------------------------------------------------------------------
    def _lock_date(self):
        """Latest date the company considers closed for a general entry."""
        self.ensure_one()
        company = self.company_id
        dates = [company.fiscalyear_lock_date, company.hard_lock_date]
        dates = [d for d in dates if d]
        return max(dates) if dates else None

    def _posting_date(self, origin_date):
        """Where the correction for ``origin_date`` is booked.

        Never lifts the lock date — a closed source period is corrected in the
        current month instead.
        """
        self.ensure_one()
        if self.posting_policy == "current":
            return self.fallback_date, origin_date is not None and self._is_closed(origin_date)
        closed = self._is_closed(origin_date)
        return (self.fallback_date if closed else origin_date), closed

    def _is_closed(self, origin_date):
        lock = self._lock_date()
        return bool(lock and origin_date and origin_date <= lock)

    # ------------------------------------------------------------------
    # What the old category actually caused to be posted
    # ------------------------------------------------------------------
    def _products(self):
        self.ensure_one()
        return (
            self.env["product.product"]
            .with_context(active_test=False)
            .search([("product_tmpl_id", "in", self.product_tmpl_ids.ids)])
        )

    _CATEG_FIELD = {
        "income": "property_account_income_categ_id",
        "discount": "property_account_sales_discount_categ_id",
        "return": "property_account_sales_return_categ_id",
        "expense": "property_account_expense_categ_id",
        "valuation": "property_stock_valuation_account_id",
    }

    def _categ_property(self, categ, kind):
        """First account of ``kind`` found walking ``categ`` up its parent chain.

        Only the COA root categories carry the mapping (see
        ``scripts/tenants/retail/34_coa_categ_tree.py``); everything below inherits.
        """
        field = self._CATEG_FIELD[kind]
        node = categ.with_company(self.company_id)
        while node:
            if node[field]:
                return node[field]
            # with_company has to be re-applied on every hop: these properties are
            # company-dependent, and a parent browsed without it would be read for
            # ``env.company`` instead — silently the wrong account.
            node = node.parent_id.with_company(self.company_id)
        return self.env["account.account"].browse()

    @api.model
    def _retail_categ_accounts(self, company, product, categ):
        """Every account ``product`` would post to if it sat in ``categ``.

        Public entry point so callers that hold no reclass record — the
        category-change guard in ``custom_retail_categ_approval``, for one — can
        ask "does moving this product actually move the GL?" without
        reimplementing the resolution and drifting from it.
        """
        probe = self.new({"company_id": company.id})
        return {kind: probe._account_for(product, categ, kind) for kind, _label in _KINDS}

    def _account_for(self, product, categ, kind):
        """Account of ``kind`` this product resolves to when it sits in ``categ``.

        For the product's *current* category this is delegated wholesale to
        ``retail.import.executor``, so what we call "the account the old category
        posted to" is by construction the account the posting code picked. For the
        hypothetical *new* category the same resolution order is replayed against
        that category, reusing the executor's own fallbacks for the tail.
        """
        self.ensure_one()
        company = self.company_id
        Executor = self.env["retail.import.executor"]
        current = product.with_company(company).categ_id == categ

        if kind in ("expense", "valuation"):
            return self._categ_property(categ, kind)

        if kind == "income":
            if current:
                return Executor._ri_income_account(company, product)
            return (
                product.with_company(company).property_account_income_id
                or self._categ_property(categ, "income")
                or Executor._ri_income_account(company, product)
            )

        # discount / return
        if current:
            return Executor._ri_category_account(company, product, kind)
        account = self._categ_property(categ, kind)
        if account:
            return account
        income = self._account_for(product, categ, "income")
        if not income:
            return self.env["account.account"].browse()
        return Executor._ri_contra_account_fallback(company, income, kind)

    def _pos_postings(self, products):
        """Recompute, per POS line, what the closing entries booked for ``products``.

        Mirrors the real posting path exactly:

        * core POS credits ``Gross Sales-<cat>`` with ``price_subtotal``, which is
          already **net of discount**;
        * ``custom_retail_import_pos.pos_order_line`` swaps the account to
          ``Sales Return-<cat>`` on ``ri_is_return`` lines;
        * ``custom_retail_import_pos.pos_session._ri_discount_reclass_line_vals``
          then grosses revenue back up — Dr ``Sales Discount-<cat>`` /
          Cr ``Gross Sales-<cat>`` — for ``ri_src_discount``.

        Yields ``(product, date, analytic_id, kind, signed balance)``.
        """
        self.ensure_one()
        company = self.company_id
        Executor = self.env["retail.import.executor"]
        discount_on = Executor._x24_discount_reclass_enabled()
        domain = [
            ("product_id", "in", products.ids),
            ("order_id.state", "in", _SOLD_STATES),
            ("order_id.company_id", "=", company.id),
            # An invoiced POS order books its revenue on the invoice, not in the
            # session's closing entry; those lines are picked up by the GL pass.
            ("order_id.account_move", "=", False),
        ]
        if self.date_from:
            domain.append(("order_id.date_order", ">=", datetime.combine(self.date_from, time.min)))
        if self.date_to:
            domain.append(("order_id.date_order", "<=", datetime.combine(self.date_to, time.max)))

        analytic_cache = {}
        for line in self.env["pos.order.line"].search(domain):
            order = line.order_id
            date = fields.Date.to_date(order.date_order)
            session = order.session_id
            if session.id not in analytic_cache:
                analytic_cache[session.id] = self._session_analytic(session)
            analytic_id = analytic_cache[session.id]
            product = line.product_id
            base_kind = "return" if line.ri_is_return else "income"
            if not float_is_zero(line.price_subtotal, precision_rounding=company.currency_id.rounding):
                yield product, date, analytic_id, base_kind, -line.price_subtotal
            if discount_on and line.ri_src_discount:
                yield product, date, analytic_id, "income", -line.ri_src_discount
                yield product, date, analytic_id, "discount", line.ri_src_discount

    def _session_analytic(self, session):
        """Operating Unit the session's closing entry actually carried.

        Read from the posted entry rather than recomputed, so the correction lands
        on the same OU even if the store's warehouse mapping has moved since.
        """
        move = session.move_id
        for line in move.line_ids:
            if line.analytic_distribution:
                keys = [int(k) for k in line.analytic_distribution]
                if keys:
                    return keys[0]
        ou = session.config_id.warehouse_id.l10n_ou_analytic_id
        return ou.id if ou else False

    def _gl_postings(self, products):
        """Posted move lines that still name a product (invoices, stock valuation).

        On ``prd_retail_begbal`` this yields nothing — every revenue line comes from
        a POS closing entry, which carries no ``product_id`` — but invoices in other
        the apparel brand databases do, and stock valuation lines always do.
        """
        self.ensure_one()
        company = self.company_id
        old_accounts = {}
        for product in products:
            for kind, _label in _KINDS:
                account = self._account_for(product, product.categ_id, kind)
                if account:
                    old_accounts.setdefault(account.id, kind)
        if not old_accounts:
            return
        domain = [
            ("product_id", "in", products.ids),
            ("account_id", "in", list(old_accounts)),
            ("parent_state", "=", "posted"),
            ("company_id", "=", company.id),
            # Never chase our own corrections.
            ("move_id.retail_categ_reclass_id", "=", False),
        ]
        if self.date_from:
            domain.append(("date", ">=", self.date_from))
        if self.date_to:
            domain.append(("date", "<=", self.date_to))
        for line in self.env["account.move.line"].search(domain):
            analytic_id = False
            if line.analytic_distribution:
                keys = [int(k) for k in line.analytic_distribution]
                analytic_id = keys[0] if keys else False
            yield line.product_id, line.date, analytic_id, old_accounts[line.account_id.id], line.balance

    def _covered_keys(self):
        """(product, date, kind) already corrected by an applied reclass.

        Recomputing after an apply must not book the same turnover twice.
        """
        self.ensure_one()
        applied = self.search(
            [("state", "=", "applied"), ("company_id", "=", self.company_id.id), ("id", "!=", self.id)]
        )
        return {(line.product_id.id, line.origin_date, line.kind) for line in applied.mapped("line_ids")}

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------
    def action_compute(self):
        for rec in self:
            if rec.state == "applied":
                raise UserError(_("%s was already applied.", rec.name))
            rec.line_ids.unlink()
            rec.product_ids.unlink()
            products = rec._products()
            if not products:
                raise UserError(_("The selected products have no variants."))
            rounding = rec.company_id.currency_id.rounding
            covered = rec._covered_keys()
            warnings = []

            buckets = defaultdict(float)
            for source in (rec._pos_postings(products), rec._gl_postings(products)):
                for product, date, analytic_id, kind, balance in source:
                    if (product.id, date, kind) in covered:
                        continue
                    buckets[(product.id, date, analytic_id, kind)] += balance

            Product = self.env["product.product"]
            account_cache = {}
            vals_list = []
            for (product_id, date, analytic_id, kind), balance in sorted(
                buckets.items(), key=lambda item: (item[0][1], item[0][3], item[0][0])
            ):
                if float_is_zero(balance, precision_rounding=rounding):
                    continue
                product = Product.browse(product_id)
                cache_key = (product_id, kind)
                if cache_key not in account_cache:
                    account_cache[cache_key] = (
                        rec._account_for(product, product.categ_id, kind),
                        rec._account_for(product, rec.new_categ_id, kind),
                    )
                source_account, target_account = account_cache[cache_key]
                if not source_account or not target_account:
                    warnings.append(
                        _(
                            "%(product)s: no %(kind)s account on the old and/or new "
                            "category — %(amount)s left untouched.",
                            product=product.display_name,
                            kind=kind,
                            amount=balance,
                        )
                    )
                    continue
                if source_account == target_account:
                    continue
                posting_date, closed = rec._posting_date(date)
                vals_list.append(
                    {
                        "reclass_id": rec.id,
                        "product_id": product_id,
                        "kind": kind,
                        "origin_date": date,
                        "posting_date": posting_date,
                        "is_period_closed": closed,
                        "analytic_account_id": analytic_id,
                        "source_account_id": source_account.id,
                        "target_account_id": target_account.id,
                        "amount": balance,
                    }
                )
            lines = self.env["retail.categ.reclass.line"].create(vals_list)
            lines._check_against_gl()

            rec.product_ids = [
                (0, 0, {"product_tmpl_id": tmpl.id, "old_categ_id": tmpl.categ_id.id}) for tmpl in rec.product_tmpl_ids
            ]
            if not lines:
                warnings.append(
                    _(
                        "Nothing was posted for these products in the selected period, "
                        "or it has already been corrected. Changing the category is "
                        "still safe — future postings will use the new accounts."
                    )
                )
            rec.warning_text = "\n".join(warnings) or False
            rec.state = "computed"
        return True

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------
    def _clearing_account(self):
        """Intermediate account for the reversal / re-booking pair.

        Resolved by code from a system parameter so the same setting works in
        every company of the 10-digit chart — same contract as
        ``custom.scrap.batch._scrap_loss_account``. Only needed when a closed
        period is involved, so it is looked up lazily rather than at create.
        """
        self.ensure_one()
        code = self.env["ir.config_parameter"].sudo().get_param(self._CLEARING_PARAM, "").strip()
        if not code:
            raise UserError(
                _(
                    "A correction that falls in a closed period is booked as a "
                    "reversal plus a re-booking, which needs a clearing account. "
                    "Set the system parameter '%s' to its account code.",
                    self._CLEARING_PARAM,
                )
            )
        account = self.env["account.account"].with_company(self.company_id).search([("code", "=", code)], limit=1)
        if not account:
            raise UserError(
                _(
                    "Clearing account code '%(code)s' was not found in company %(company)s.",
                    code=code,
                    company=self.company_id.display_name,
                )
            )
        return account

    def action_apply(self):
        self.ensure_one()
        if self.state == "applied":
            raise UserError(_("%s was already applied.", self.name))
        if self.state == "draft":
            self.action_compute()

        # The category change itself. Recorded per template first so a cancel can
        # put it back.
        for entry in self.product_ids:
            entry.product_tmpl_id.categ_id = self.new_categ_id.id

        open_lines = self.line_ids.filtered(lambda line: not line.is_period_closed)
        closed_lines = self.line_ids - open_lines
        moves = self._book_open_period(open_lines) | self._book_closed_period(closed_lines)
        self.state = "applied"
        return bool(moves) or True

    def _book_open_period(self, lines):
        """One net entry per original date: Dr account-was / Cr account-should-be.

        The period is still open, so the correction lands exactly where the
        turnover was booked and that month's P&L is right without a footnote.
        """
        currency = self.company_id.currency_id
        by_date = defaultdict(lambda: defaultdict(float))
        for line in lines:
            key = (line.source_account_id.id, line.target_account_id.id, line.analytic_account_id.id)
            by_date[line.posting_date][key] += line.amount

        moves = self.env["account.move"]
        label = _("Category reclass %s", self.name)
        for posting_date in sorted(by_date):
            move_lines = []
            for (source_id, target_id, analytic_id), amount in by_date[posting_date].items():
                if float_is_zero(amount, precision_rounding=currency.rounding):
                    continue
                analytic = {str(analytic_id): 100.0} if analytic_id else False
                # The source account gives up the balance it wrongly holds; the
                # target takes it on. Both legs carry the store's OU so the per-OU
                # P&L moves with the revenue instead of being left behind.
                move_lines.append((0, 0, self._line_vals(source_id, label, -amount, analytic)))
                move_lines.append((0, 0, self._line_vals(target_id, label, amount, analytic)))
            if move_lines:
                moves |= self._create_move(posting_date, self._default_ref(), move_lines)
        return moves

    def _book_closed_period(self, lines):
        """Two entries in the current month, per closed source period.

            REVERSAL     Dr Gross Sales-was      / Cr Clearing
            RE-BOOKING   Dr Clearing             / Cr Gross Sales-should-be

        A closed month must not be reopened, so nothing is booked back into it.
        Splitting the move in two makes the correction legible to an auditor —
        one entry undoes what the wrong category caused, the other states what
        the right one would have caused — and the clearing account nets to zero
        between them. Grouped per source period (YYYY-MM) rather than per day,
        because the day no longer means anything once the date has moved.
        """
        if not lines:
            return self.env["account.move"]
        currency = self.company_id.currency_id
        clearing = self._clearing_account()
        by_period = defaultdict(lambda: defaultdict(float))
        for line in lines:
            period = line.origin_date.strftime("%Y-%m")
            key = (
                line.posting_date,
                period,
                line.source_account_id.id,
                line.target_account_id.id,
                line.analytic_account_id.id,
            )
            by_period[(line.posting_date, period)][key[2:]] += line.amount

        moves = self.env["account.move"]
        for posting_date, period in sorted(by_period):
            reversal_lines, rebooking_lines = [], []
            for (source_id, target_id, analytic_id), amount in by_period[(posting_date, period)].items():
                if float_is_zero(amount, precision_rounding=currency.rounding):
                    continue
                analytic = {str(analytic_id): 100.0} if analytic_id else False
                rev_label = _("Reversal %(period)s — %(name)s", period=period, name=self.name)
                reb_label = _("Re-booking %(period)s — %(name)s", period=period, name=self.name)
                reversal_lines.append((0, 0, self._line_vals(source_id, rev_label, -amount, analytic)))
                reversal_lines.append((0, 0, self._line_vals(clearing.id, rev_label, amount, analytic)))
                rebooking_lines.append((0, 0, self._line_vals(clearing.id, reb_label, -amount, analytic)))
                rebooking_lines.append((0, 0, self._line_vals(target_id, reb_label, amount, analytic)))
            if not reversal_lines:
                continue
            moves |= self._create_move(
                posting_date,
                _("CATEG-RECLASS %(name)s REVERSAL %(period)s", name=self.name, period=period),
                reversal_lines,
            )
            moves |= self._create_move(
                posting_date,
                _("CATEG-RECLASS %(name)s RE-BOOKING %(period)s", name=self.name, period=period),
                rebooking_lines,
            )
        return moves

    def _default_ref(self):
        return _("CATEG-RECLASS %(name)s → %(categ)s", name=self.name, categ=self.new_categ_id.name)

    def _create_move(self, date, ref, move_lines):
        return self.env["account.move"].create(
            {
                "move_type": "entry",
                "journal_id": self.journal_id.id,
                "company_id": self.company_id.id,
                "date": date,
                "ref": ref,
                "retail_categ_reclass_id": self.id,
                "line_ids": move_lines,
            }
        )

    def _line_vals(self, account_id, label, balance, analytic):
        return {
            "account_id": account_id,
            "name": label[:200],
            "debit": balance if balance > 0 else 0.0,
            "credit": -balance if balance < 0 else 0.0,
            "analytic_distribution": dict(analytic) if analytic else False,
        }

    def action_cancel(self):
        self.ensure_one()
        if self.move_ids.filtered(lambda m: m.state != "draft"):
            raise UserError(
                _(
                    "Journal entries of %s are already posted. Reverse them from the "
                    "entry itself; this record cannot undo a posted correction.",
                    self.name,
                )
            )
        self.move_ids.unlink()
        for entry in self.product_ids:
            if entry.old_categ_id:
                entry.product_tmpl_id.categ_id = entry.old_categ_id.id
        self.state = "cancel"
        return True

    def action_view_moves(self):
        self.ensure_one()
        action = {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "name": _("Correction Entries"),
            "domain": [("id", "in", self.move_ids.ids)],
            "view_mode": "list,form",
        }
        if len(self.move_ids) == 1:
            action.update({"view_mode": "form", "res_id": self.move_ids.id})
        return action


class RetailCategReclassLine(models.Model):
    _name = "retail.categ.reclass.line"
    _description = "Product Category Reclassification Line"
    _order = "posting_date, kind, product_id"

    reclass_id = fields.Many2one("retail.categ.reclass", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="reclass_id.company_id", store=True)
    currency_id = fields.Many2one(related="reclass_id.currency_id")

    product_id = fields.Many2one("product.product", required=True)
    kind = fields.Selection(_KINDS, required=True)
    origin_date = fields.Date(string="Source Date", required=True)
    posting_date = fields.Date(required=True)
    is_period_closed = fields.Boolean(
        string="Source Period Closed",
        help="The source period is locked, so this correction is booked in the "
        "current month instead of on its own date.",
    )
    analytic_account_id = fields.Many2one("account.analytic.account", string="Operating Unit")
    source_account_id = fields.Many2one("account.account", string="From Account", required=True)
    target_account_id = fields.Many2one("account.account", string="To Account", required=True)
    amount = fields.Monetary(
        currency_field="currency_id",
        help="Signed balance to move. Revenue is negative (a credit balance).",
    )
    is_matched = fields.Boolean(
        default=True,
        string="Backed by GL",
        help="The source account really holds at least this much on the source "
        "date. Unticked means the recomputation and the GL disagree — check "
        "before posting.",
    )

    def _check_against_gl(self):
        """Flag lines the general ledger cannot back.

        Cheap sanity net: if ``Gross Sales-Labor (Service)`` never held the amount
        being taken off it on that day, the recomputation is wrong (a category was
        already changed once, an import was re-run, …) and the correction would
        invent a balance.
        """
        if not self:
            return
        grouped = defaultdict(float)
        for line in self:
            grouped[(line.source_account_id.id, line.origin_date)] += line.amount
        posted = defaultdict(float)
        read = self.env["account.move.line"]._read_group(
            [
                ("account_id", "in", list({key[0] for key in grouped})),
                ("date", "in", list({key[1] for key in grouped})),
                ("parent_state", "=", "posted"),
                ("company_id", "in", self.company_id.ids),
            ],
            # Odoo 19 refuses a date groupby without a granularity.
            ["account_id", "date:day"],
            ["balance:sum"],
        )
        for account, date, balance in read:
            posted[(account.id, date)] = balance
        rounding = self.company_id[:1].currency_id.rounding or 0.01
        for line in self:
            key = (line.source_account_id.id, line.origin_date)
            available = posted.get(key, 0.0)
            # Same sign and big enough in absolute terms.
            ok = (
                available * grouped[key] > 0
                and float_compare(abs(available), abs(grouped[key]), precision_rounding=rounding) >= 0
            )
            line.is_matched = bool(ok)


class RetailCategReclassProduct(models.Model):
    _name = "retail.categ.reclass.product"
    _description = "Product Category Reclassification — Category Change"
    _order = "id"

    reclass_id = fields.Many2one("retail.categ.reclass", required=True, ondelete="cascade")
    product_tmpl_id = fields.Many2one("product.template", required=True)
    old_categ_id = fields.Many2one("product.category", string="Previous Category")


class AccountMove(models.Model):
    _inherit = "account.move"

    retail_categ_reclass_id = fields.Many2one(
        "retail.categ.reclass",
        string="Category Reclassification",
        readonly=True,
        index="btree_not_null",
        help="The category correction that produced this entry.",
    )
