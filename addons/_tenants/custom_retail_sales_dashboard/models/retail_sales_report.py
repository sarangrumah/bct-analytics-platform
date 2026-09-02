# -*- coding: utf-8 -*-
"""Sales Analysis — read-only SQL view over the imported POS lines, plus the
single RPC that feeds the Sales Command Centre.

One row per ``pos.order.line``. The view resolves, in one pass, the five joins
every retail question needs (store, operating unit, product, COA revenue root,
transaction header) and normalises the amounts:

* **Amounts come from the source workbook** where the importer supplied them
  (``ri_src_net`` / ``ri_src_tax`` / ``ri_src_discount``), falling back to
  Odoo's own subtotals otherwise. X24DN truncates net per line while Odoo rounds
  tax per order, so preferring the file's figures is what makes this view tie
  out against the GL that ``custom_retail_import_pos`` posts from the same
  numbers. A database mixing imported and natively-rung orders still totals.
* **``date`` is the Jakarta trading date.** ``pos_order.date_order`` is a naive
  UTC timestamp; the importer stamps 12:00 UTC so a naive cast would happen to
  be right, but a live order rung at 23:30 WIB would be reported a day early.
* **``order_count`` is 1 on exactly one line per order** (lowest line id, via a
  window function), so *transactions* is a summable measure in the pivot instead
  of a number only the dashboard knows how to compute. Nothing else can count
  distinct orders inside a group-by.

Returns keep their source sign (``ri_src_net`` is negative for a refund), so
``net_amount`` sums to net-of-returns on its own. ``sale_amount`` /
``return_amount`` split the two apart as positive magnitudes for the rate.
"""

from datetime import timedelta

from odoo import _, api, fields, models, tools
from odoo.tools import SQL
from odoo.tools.sql import column_exists

# Trading timezone. the apparel brand Indonesia books its day in WIB; a store's "3 June"
# is 2 June 17:00 UTC .. 3 June 17:00 UTC. Held as a constant rather than an
# ir.config_parameter because init() runs before data files load, so a parameter
# would apply on -u but not on a fresh -i — a difference nobody would notice
# until two databases disagreed about a Monday.
REPORT_TZ = "Asia/Jakarta"

# POS states that represent a completed sale. Mirrors custom_retail_localization's
# retail.cogs.run, so COGS and revenue are drawn from the same population.
SOLD_STATES = ("paid", "done", "invoiced")

# Rows returned by the "top N" tables on the dashboard.
TOP_N = 10


class RetailSalesReport(models.Model):
    _name = "retail.sales.report"
    _description = "the apparel brand Sales Analysis"
    _auto = False
    _rec_name = "product_id"
    _order = "date desc, id desc"
    # An _auto=False model only flushes itself, so an order posted earlier in the
    # same transaction would be invisible through the view. Naming the underlying
    # models makes the ORM flush them before the query runs.
    _depends = {
        "pos.order": [
            "session_id", "date_order", "state", "company_id", "pos_reference",
            "ri_member_id", "ri_member_type", "ri_omni_order_id",
            "ri_staff_id", "ri_staff_name",
        ],
        "pos.order.line": [
            "order_id", "product_id", "qty", "price_subtotal", "price_subtotal_incl",
            "ri_src_net", "ri_src_tax", "ri_src_discount", "ri_is_return",
            "ri_staff_id", "ri_staff_name", "ri_discount_code", "ri_discount_type",
        ],
        "pos.session": ["config_id"],
        "product.product": ["default_code", "product_tmpl_id"],
        "product.template": ["categ_id"],
    }

    # --- Dimensions ---------------------------------------------------------
    date = fields.Date(string="Trading Date", readonly=True, index=True)
    date_order = fields.Datetime(string="Order Time (WIB)", readonly=True)
    day_of_week = fields.Integer(string="Day of Week", readonly=True, aggregator=None)
    order_id = fields.Many2one("pos.order", string="Transaction", readonly=True)
    pos_reference = fields.Char(string="Receipt", readonly=True)
    session_id = fields.Many2one("pos.session", string="Session", readonly=True)
    config_id = fields.Many2one("pos.config", string="Point of Sale", readonly=True)
    warehouse_id = fields.Many2one("stock.warehouse", string="Store", readonly=True)
    analytic_account_id = fields.Many2one(
        "account.analytic.account", string="Operating Unit", readonly=True
    )
    product_id = fields.Many2one("product.product", string="Product", readonly=True)
    product_tmpl_id = fields.Many2one("product.template", string="Product Template", readonly=True)
    default_code = fields.Char(string="SKU", readonly=True)
    categ_id = fields.Many2one("product.category", string="Category", readonly=True)
    categ_root_id = fields.Many2one(
        "product.category",
        string="Revenue Bucket",
        readonly=True,
        help="Top-level product category — the COA revenue bucket the sale lands on "
        "(Textile / Footwear / Accessories / ...).",
    )
    staff_code = fields.Char(string="Cashier ID", readonly=True)
    staff_name = fields.Char(string="Cashier", readonly=True)
    member_type = fields.Char(string="Member Type", readonly=True)
    is_member = fields.Boolean(string="Member Sale", readonly=True)
    discount_code = fields.Char(string="Promo Code", readonly=True)
    discount_type = fields.Char(string="Promo Type", readonly=True)
    has_discount = fields.Boolean(string="Discounted", readonly=True)
    is_return = fields.Boolean(string="Return", readonly=True)
    is_omni = fields.Boolean(string="Omnichannel", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)

    # --- Measures -----------------------------------------------------------
    qty = fields.Float(string="Units", readonly=True)
    gross_amount = fields.Monetary(string="Gross Sales", readonly=True)
    discount_amount = fields.Monetary(string="Discount", readonly=True)
    net_amount = fields.Monetary(string="Net Sales", readonly=True)
    tax_amount = fields.Monetary(string="Tax (PPN)", readonly=True)
    total_amount = fields.Monetary(string="Total Incl. Tax", readonly=True)
    sale_amount = fields.Monetary(
        string="Sales", readonly=True, help="Net amount of non-return lines only."
    )
    return_amount = fields.Monetary(
        string="Returns", readonly=True, help="Net amount of return lines, as a positive magnitude."
    )
    order_count = fields.Integer(
        string="Transactions",
        readonly=True,
        help="1 on the first line of each transaction, 0 elsewhere — sum it to count transactions.",
    )
    line_count = fields.Integer(string="Lines", readonly=True)

    # ------------------------------------------------------------------
    # View definition
    # ------------------------------------------------------------------
    def _warehouse_expr(self):
        """Resolve a POS config to its warehouse.

        ``pos.config.warehouse_id`` is the direct link, but it has moved between
        stored column and related field across Odoo versions and this view must
        not fail at install time over that. The picking type always carries the
        warehouse, so it backs the direct column up (and fills in for a config
        whose warehouse was never set).
        """
        if column_exists(self.env.cr, "pos_config", "warehouse_id"):
            return "COALESCE(pcfg.warehouse_id, spt.warehouse_id)"
        return "spt.warehouse_id"

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        # Jakarta-local order timestamp, reused by date / dow / hour.
        local_ts = f"(po.date_order AT TIME ZONE 'UTC' AT TIME ZONE '{REPORT_TZ}')"
        net = "COALESCE(pol.ri_src_net, pol.price_subtotal)"
        tax = "COALESCE(pol.ri_src_tax, pol.price_subtotal_incl - pol.price_subtotal)"
        disc = "COALESCE(pol.ri_src_discount, 0.0)"
        is_return = "(COALESCE(pol.ri_is_return, FALSE) OR pol.qty < 0)"
        states = ", ".join("'%s'" % s for s in SOLD_STATES)

        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT
                    pol.id                                  AS id,
                    pol.order_id                            AS order_id,
                    po.pos_reference                        AS pos_reference,
                    po.session_id                           AS session_id,
                    ps.config_id                            AS config_id,
                    wh.id                                   AS warehouse_id,
                    wh.l10n_ou_analytic_id                  AS analytic_account_id,
                    {local_ts}                              AS date_order,
                    {local_ts}::date                        AS date,
                    EXTRACT(ISODOW FROM {local_ts})::int     AS day_of_week,
                    pol.product_id                          AS product_id,
                    pt.id                                   AS product_tmpl_id,
                    pp.default_code                         AS default_code,
                    pt.categ_id                             AS categ_id,
                    NULLIF(split_part(pcat.parent_path, '/', 1), '')::int
                                                            AS categ_root_id,
                    COALESCE(NULLIF(pol.ri_staff_id, ''), NULLIF(po.ri_staff_id, ''))
                                                            AS staff_code,
                    COALESCE(NULLIF(pol.ri_staff_name, ''), NULLIF(po.ri_staff_name, ''))
                                                            AS staff_name,
                    NULLIF(po.ri_member_type, '')           AS member_type,
                    (NULLIF(po.ri_member_id, '') IS NOT NULL)   AS is_member,
                    NULLIF(pol.ri_discount_code, '')        AS discount_code,
                    NULLIF(pol.ri_discount_type, '')        AS discount_type,
                    (COALESCE({disc}, 0.0) <> 0.0)          AS has_discount,
                    {is_return}                             AS is_return,
                    (NULLIF(po.ri_omni_order_id, '') IS NOT NULL) AS is_omni,
                    pol.qty                                 AS qty,
                    ({net} + {disc})                        AS gross_amount,
                    {disc}                                  AS discount_amount,
                    {net}                                   AS net_amount,
                    {tax}                                   AS tax_amount,
                    ({net} + {tax})                         AS total_amount,
                    CASE WHEN {is_return} THEN 0.0 ELSE {net} END   AS sale_amount,
                    CASE WHEN {is_return} THEN -({net}) ELSE 0.0 END AS return_amount,
                    CASE
                        WHEN pol.id = MIN(pol.id) OVER (PARTITION BY pol.order_id)
                        THEN 1 ELSE 0
                    END                                     AS order_count,
                    1                                       AS line_count,
                    po.company_id                           AS company_id,
                    rc.currency_id                          AS currency_id
                FROM pos_order_line pol
                JOIN pos_order po        ON po.id = pol.order_id
                JOIN pos_session ps      ON ps.id = po.session_id
                JOIN pos_config pcfg     ON pcfg.id = ps.config_id
                JOIN res_company rc      ON rc.id = po.company_id
                LEFT JOIN stock_picking_type spt ON spt.id = pcfg.picking_type_id
                LEFT JOIN stock_warehouse wh ON wh.id = {self._warehouse_expr()}
                JOIN product_product pp  ON pp.id = pol.product_id
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                LEFT JOIN product_category pcat ON pcat.id = pt.categ_id
                WHERE po.state IN ({states})
            )
            """
        )

    # ==================================================================
    # Dashboard RPC
    # ==================================================================
    # One call returns every tile. The alternative — a searchRead per widget —
    # costs a dozen round trips against a table with millions of POS lines, and
    # each one re-scans the same date range.
    # ------------------------------------------------------------------

    @api.model
    def get_dashboard(self, options=None):
        """Return the full Sales Command Centre payload for ``options``.

        ``options`` (all optional)::

            {
              "date_from": "2026-08-01", "date_to": "2026-08-14",
              "compare": "previous" | "year" | "none",
              "warehouse_ids": [1, 2], "categ_ids": [5],
              "channel": "all" | "store" | "omni",
              "include_returns": true
            }
        """
        opt = self._normalise_options(options)
        date_from, date_to = opt["date_from"], opt["date_to"]
        cmp_from, cmp_to = self._comparison_window(date_from, date_to, opt["compare"])

        company = self.env.company
        current = self._aggregate_totals(opt, date_from, date_to)
        previous = (
            self._aggregate_totals(opt, cmp_from, cmp_to)
            if cmp_from
            else self._empty_totals()
        )

        targets = self.env["retail.sales.target"].prorated_targets(
            date_from, date_to, opt["warehouse_ids"] or None, opt["company_ids"]
        )
        target_amount = sum(t["amount"] for t in targets.values())

        return {
            "options": {
                **opt,
                "date_from": fields.Date.to_string(date_from),
                "date_to": fields.Date.to_string(date_to),
                "compare_from": fields.Date.to_string(cmp_from) if cmp_from else None,
                "compare_to": fields.Date.to_string(cmp_to) if cmp_to else None,
                "company_ids": opt["company_ids"],
            },
            "currency": {
                "id": company.currency_id.id,
                "symbol": company.currency_id.symbol,
                "position": company.currency_id.position,
                "decimals": company.currency_id.decimal_places,
            },
            "kpi": self._build_kpis(current, previous, target_amount),
            "trend": self._trend(opt, date_from, date_to, cmp_from, cmp_to, targets),
            "stores": self._by_store(opt, date_from, date_to, cmp_from, cmp_to, targets),
            "categories": self._by_category(opt, date_from, date_to),
            "dow": self._by_day_of_week(opt, date_from, date_to),
            "products": self._top_products(opt, date_from, date_to),
            "staff": self._top_staff(opt, date_from, date_to),
            "promos": self._top_promos(opt, date_from, date_to),
            "mix": self._channel_mix(opt, date_from, date_to),
            "filters": self._filter_choices(opt),
        }

    @api.model
    def action_drill(self, domain, title=None, view="pivot"):
        """Open the Sales Analysis view filtered to what was clicked.

        The dashboard is a starting point, not a terminus: every tile, bar and
        row hands its own domain to this so the underlying lines stay one click
        away.
        """
        self.check_access("read")
        views = [
            (False, "pivot"),
            (False, "graph"),
            (False, "list"),
        ]
        if view == "list":
            views = [(False, "list"), (False, "pivot"), (False, "graph")]
        return {
            "type": "ir.actions.act_window",
            "name": title or _("Sales Analysis"),
            "res_model": self._name,
            "views": views,
            "domain": domain or [],
            "target": "current",
            "context": {"search_default_group_date": 1},
        }

    # ------------------------------------------------------------------
    # Option handling
    # ------------------------------------------------------------------
    @api.model
    def _normalise_options(self, options):
        """Coerce whatever the client sent into a trusted, typed filter set.

        Everything downstream builds raw SQL, so nothing from the client is ever
        interpolated: ids are cast to int, enumerations are matched against a
        fixed set, and dates go through the ORM's own parser.
        """
        opt = dict(options or {})
        today = fields.Date.context_today(self)

        def as_date(value, fallback):
            if not value:
                return fallback
            try:
                return fields.Date.to_date(value)
            except (ValueError, TypeError):
                return fallback

        date_from = as_date(opt.get("date_from"), today.replace(day=1))
        date_to = as_date(opt.get("date_to"), today)
        if date_to < date_from:
            date_from, date_to = date_to, date_from

        compare = opt.get("compare") or "previous"
        if compare not in ("previous", "year", "none"):
            compare = "previous"
        channel = opt.get("channel") or "all"
        if channel not in ("all", "store", "omni"):
            channel = "all"

        def as_ids(value):
            return [int(v) for v in (value or []) if str(v).lstrip("-").isdigit()]

        # A bucket is picked at the root; the sales sit on its leaves. The picked
        # ids are kept separately from the expanded ones so the client gets its
        # own selection echoed back rather than a few hundred leaf categories.
        categ_ids = as_ids(opt.get("categ_ids"))
        categ_leaf_ids = (
            self.env["product.category"].search([("id", "child_of", categ_ids)]).ids
            if categ_ids
            else []
        )

        return {
            "date_from": date_from,
            "date_to": date_to,
            "compare": compare,
            "channel": channel,
            "warehouse_ids": as_ids(opt.get("warehouse_ids")),
            "categ_ids": categ_ids,
            "categ_leaf_ids": categ_leaf_ids,
            "include_returns": bool(opt.get("include_returns", True)),
            "company_ids": self.env.companies.ids,
        }

    @api.model
    def _comparison_window(self, date_from, date_to, compare):
        """The window the current period is measured against.

        ``previous`` is the immediately preceding window of the same length —
        the right comparison for a promo or a short range. ``year`` is the same
        calendar dates a year earlier, which is what retail actually steers on,
        seasonality being the dominant signal in fashion.
        """
        if compare == "none":
            return None, None
        if compare == "year":
            def shift(d):
                try:
                    return d.replace(year=d.year - 1)
                except ValueError:  # 29 February
                    return d.replace(year=d.year - 1, day=28)
            return shift(date_from), shift(date_to)
        span = (date_to - date_from).days + 1
        return date_from - timedelta(days=span), date_from - timedelta(days=1)

    # ------------------------------------------------------------------
    # SQL helpers
    # ------------------------------------------------------------------
    def _where(self, opt, date_from, date_to, alias="r"):
        """Build the shared WHERE clause as a parameterised :class:`SQL`."""
        conditions = [
            SQL("%s.company_id = ANY(%s)", SQL.identifier(alias), opt["company_ids"]),
            SQL("%s.date >= %s", SQL.identifier(alias), date_from),
            SQL("%s.date <= %s", SQL.identifier(alias), date_to),
        ]
        if opt["warehouse_ids"]:
            conditions.append(
                SQL("%s.warehouse_id = ANY(%s)", SQL.identifier(alias), opt["warehouse_ids"])
            )
        if opt["categ_leaf_ids"]:
            conditions.append(
                SQL("%s.categ_id = ANY(%s)", SQL.identifier(alias), opt["categ_leaf_ids"])
            )
        if opt["channel"] == "omni":
            conditions.append(SQL("%s.is_omni", SQL.identifier(alias)))
        elif opt["channel"] == "store":
            conditions.append(SQL("NOT %s.is_omni", SQL.identifier(alias)))
        if not opt["include_returns"]:
            conditions.append(SQL("NOT %s.is_return", SQL.identifier(alias)))
        return SQL(" AND ").join(conditions)

    def _query(self, sql):
        self.env.cr.execute(sql)
        return self.env.cr.dictfetchall()

    # The measure block every aggregate reuses, so a KPI, a store row and a day
    # on the trend line are computed from one definition.
    _MEASURES = """
        COALESCE(SUM(r.net_amount), 0.0)      AS net,
        COALESCE(SUM(r.gross_amount), 0.0)    AS gross,
        COALESCE(SUM(r.discount_amount), 0.0) AS discount,
        COALESCE(SUM(r.tax_amount), 0.0)      AS tax,
        COALESCE(SUM(r.sale_amount), 0.0)     AS sales,
        COALESCE(SUM(r.return_amount), 0.0)   AS returns,
        COALESCE(SUM(r.qty), 0.0)             AS units,
        COALESCE(SUM(r.order_count), 0)       AS transactions,
        COALESCE(SUM(r.order_count) FILTER (WHERE r.is_member), 0) AS member_transactions,
        COALESCE(SUM(r.net_amount) FILTER (WHERE r.has_discount), 0.0) AS discounted_net
    """

    @api.model
    def _empty_totals(self):
        return {
            "net": 0.0, "gross": 0.0, "discount": 0.0, "tax": 0.0,
            "sales": 0.0, "returns": 0.0, "units": 0.0,
            "transactions": 0, "member_transactions": 0, "discounted_net": 0.0,
        }

    def _aggregate_totals(self, opt, date_from, date_to):
        rows = self._query(
            SQL(
                "SELECT %s FROM %s r WHERE %s",
                SQL(self._MEASURES),
                SQL.identifier(self._table),
                self._where(opt, date_from, date_to),
            )
        )
        return rows[0] if rows else self._empty_totals()

    # ------------------------------------------------------------------
    # Payload builders
    # ------------------------------------------------------------------
    @staticmethod
    def _ratio(numerator, denominator):
        return (numerator / denominator) if denominator else 0.0

    @classmethod
    def _derive(cls, totals):
        """The retail ratios everyone asks for, derived once from the raw sums."""
        tx = totals["transactions"] or 0
        units = totals["units"] or 0.0
        return {
            **totals,
            "atv": cls._ratio(totals["net"], tx),
            "upt": cls._ratio(units, tx),
            "asp": cls._ratio(totals["net"], units),
            "discount_pct": cls._ratio(totals["discount"], totals["gross"]) * 100.0,
            "return_pct": cls._ratio(totals["returns"], totals["sales"]) * 100.0,
            "member_pct": cls._ratio(totals["member_transactions"], tx) * 100.0,
        }

    def _build_kpis(self, current, previous, target_amount):
        cur = self._derive(current)
        prev = self._derive(previous)
        # A target of zero is not a target: it would make every store read as
        # infinitely over-achieving. Treat it the same as "none set".
        has_target = bool(target_amount)

        def tile(key, label, value_key, fmt, invert=False, hint=""):
            value = cur[value_key]
            base = prev[value_key]
            return {
                "key": key,
                "label": label,
                "value": value,
                "previous": base,
                # No prior-period base means "new", not "+100%" — the client
                # renders a dash rather than an invented growth number.
                "delta_pct": self._ratio(value - base, abs(base)) * 100.0 if base else None,
                "format": fmt,
                # For discount and return rate, down is good.
                "invert": invert,
                "hint": hint,
            }

        kpis = [
            tile("net", "Net Sales", "net", "money", hint="Excl. PPN, net of returns"),
            tile("transactions", "Transactions", "transactions", "int", hint="Distinct POS receipts"),
            tile("units", "Units Sold", "units", "float", hint="Net of returned units"),
            tile("atv", "ATV", "atv", "money", hint="Average transaction value"),
            tile("upt", "UPT", "upt", "decimal", hint="Units per transaction"),
            tile("asp", "ASP", "asp", "money", hint="Average selling price per unit"),
            tile("discount_pct", "Discount Depth", "discount_pct", "pct", invert=True,
                 hint="Discount as a share of gross sales"),
            tile("return_pct", "Return Rate", "return_pct", "pct", invert=True,
                 hint="Returns as a share of gross-of-returns sales"),
            tile("member_pct", "Member Share", "member_pct", "pct",
                 hint="Transactions carrying a member id"),
        ]
        return {
            "tiles": kpis,
            "target": {
                "amount": target_amount,
                # Attainment is only meaningful where somebody actually set a
                # target; an unset store must not read as 0% achieved.
                "attainment_pct": self._ratio(cur["net"], target_amount) * 100.0 if has_target else None,
                "gap": target_amount - cur["net"] if has_target else None,
                "has_target": has_target,
            },
            "totals": cur,
            "previous": prev,
        }

    def _trend(self, opt, date_from, date_to, cmp_from, cmp_to, targets):
        """Daily net sales, the comparison period aligned day-for-day, and pace.

        The comparison series is aligned by *offset from the period start*, not
        by calendar date: a 14-day window compared against the previous 14 days
        has no overlapping dates, and comparing "same weekday" would drift.
        """
        rows = self._query(
            SQL(
                "SELECT r.date AS d, %s FROM %s r WHERE %s GROUP BY r.date ORDER BY r.date",
                SQL(self._MEASURES),
                SQL.identifier(self._table),
                self._where(opt, date_from, date_to),
            )
        )
        by_date = {r["d"]: r for r in rows}

        prev_by_offset = {}
        if cmp_from:
            for row in self._query(
                SQL(
                    "SELECT r.date AS d, %s FROM %s r WHERE %s GROUP BY r.date ORDER BY r.date",
                    SQL(self._MEASURES),
                    SQL.identifier(self._table),
                    self._where(opt, cmp_from, cmp_to),
                )
            ):
                prev_by_offset[(row["d"] - cmp_from).days] = row

        # Flat pace: the prorated target spread evenly over the window. See
        # retail.sales.target for why no day-of-week curve is applied.
        span = (date_to - date_from).days
        target_total = sum(t["amount"] for t in targets.values())
        pace = target_total / (span + 1) if target_total else 0.0

        series, cumulative, cumulative_target = [], 0.0, 0.0
        for offset in range(span + 1):
            day = date_from + timedelta(days=offset)
            row = by_date.get(day)
            prev_row = prev_by_offset.get(offset)
            net = row["net"] if row else 0.0
            cumulative += net
            cumulative_target += pace
            series.append({
                "date": fields.Date.to_string(day),
                "dow": day.isoweekday(),
                "net": net,
                "units": row["units"] if row else 0.0,
                "transactions": row["transactions"] if row else 0,
                "previous": prev_row["net"] if prev_row else None,
                "previous_date": (
                    fields.Date.to_string(cmp_from + timedelta(days=offset)) if cmp_from else None
                ),
                "cumulative": cumulative,
                "target": pace or None,
                "target_cumulative": cumulative_target if pace else None,
            })
        return series

    def _by_store(self, opt, date_from, date_to, cmp_from, cmp_to, targets):
        rows = self._query(
            SQL(
                """
                SELECT r.warehouse_id AS wid, %s
                  FROM %s r
                 WHERE %s AND r.warehouse_id IS NOT NULL
              GROUP BY r.warehouse_id
                """,
                SQL(self._MEASURES),
                SQL.identifier(self._table),
                self._where(opt, date_from, date_to),
            )
        )
        prev = {}
        if cmp_from:
            prev = {
                row["wid"]: row
                for row in self._query(
                    SQL(
                        """
                        SELECT r.warehouse_id AS wid, %s
                          FROM %s r
                         WHERE %s AND r.warehouse_id IS NOT NULL
                      GROUP BY r.warehouse_id
                        """,
                        SQL(self._MEASURES),
                        SQL.identifier(self._table),
                        self._where(opt, cmp_from, cmp_to),
                    )
                )
            }
        names = {
            wh.id: wh.display_name
            for wh in self.env["stock.warehouse"].browse([r["wid"] for r in rows]).exists()
        }

        out = []
        for row in rows:
            derived = self._derive(row)
            base = prev.get(row["wid"])
            target = targets.get(row["wid"], {}).get("amount") or 0.0
            out.append({
                "id": row["wid"],
                "name": names.get(row["wid"], "?"),
                "net": derived["net"],
                "units": derived["units"],
                "transactions": derived["transactions"],
                "atv": derived["atv"],
                "upt": derived["upt"],
                "discount_pct": derived["discount_pct"],
                "return_pct": derived["return_pct"],
                "previous": base["net"] if base else None,
                "delta_pct": (
                    self._ratio(derived["net"] - base["net"], abs(base["net"])) * 100.0
                    if base and base["net"] else None
                ),
                "target": target or None,
                "attainment_pct": self._ratio(derived["net"], target) * 100.0 if target else None,
            })
        out.sort(key=lambda s: s["net"], reverse=True)
        return out

    def _by_category(self, opt, date_from, date_to):
        """Mix over the COA revenue roots, which is how the P&L reads it."""
        rows = self._query(
            SQL(
                """
                SELECT COALESCE(r.categ_root_id, r.categ_id) AS cid, %s
                  FROM %s r
                 WHERE %s
              GROUP BY COALESCE(r.categ_root_id, r.categ_id)
                """,
                SQL(self._MEASURES),
                SQL.identifier(self._table),
                self._where(opt, date_from, date_to),
            )
        )
        names = {
            c.id: c.display_name
            for c in self.env["product.category"]
            .browse([r["cid"] for r in rows if r["cid"]])
            .exists()
        }
        total = sum(r["net"] for r in rows) or 0.0
        out = [{
            "id": r["cid"],
            "name": names.get(r["cid"], "Uncategorised"),
            "net": r["net"],
            "units": r["units"],
            "share_pct": self._ratio(r["net"], total) * 100.0,
        } for r in rows]
        out.sort(key=lambda c: c["net"], reverse=True)
        return out

    def _by_day_of_week(self, opt, date_from, date_to):
        """Average net sales per weekday — which trading days actually carry the week.

        Divided by the number of that weekday in the window, otherwise a range
        containing five Saturdays and four Sundays makes Saturday look stronger
        than it is.
        """
        rows = self._query(
            SQL(
                "SELECT r.day_of_week AS dow, %s FROM %s r WHERE %s GROUP BY r.day_of_week",
                SQL(self._MEASURES),
                SQL.identifier(self._table),
                self._where(opt, date_from, date_to),
            )
        )
        occurrences = {d: 0 for d in range(1, 8)}
        cursor = date_from
        while cursor <= date_to:
            occurrences[cursor.isoweekday()] += 1
            cursor += timedelta(days=1)

        labels = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
        by_dow = {r["dow"]: r for r in rows}
        return [{
            "dow": d,
            "label": labels[d],
            "net": by_dow[d]["net"] if d in by_dow else 0.0,
            "avg_net": self._ratio(by_dow[d]["net"] if d in by_dow else 0.0, occurrences[d]),
            "transactions": by_dow[d]["transactions"] if d in by_dow else 0,
            "days": occurrences[d],
        } for d in range(1, 8)]

    def _top_products(self, opt, date_from, date_to):
        rows = self._query(
            SQL(
                """
                SELECT r.product_id AS pid, MAX(r.default_code) AS code, %s
                  FROM %s r
                 WHERE %s
              GROUP BY r.product_id
              ORDER BY SUM(r.net_amount) DESC
                 LIMIT %s
                """,
                SQL(self._MEASURES),
                SQL.identifier(self._table),
                self._where(opt, date_from, date_to),
                TOP_N,
            )
        )
        names = {
            p.id: p.display_name
            for p in self.env["product.product"].browse([r["pid"] for r in rows]).exists()
        }
        return [{
            "id": r["pid"],
            "name": names.get(r["pid"], "?"),
            "code": r["code"] or "",
            "net": r["net"],
            "units": r["units"],
            "asp": self._ratio(r["net"], r["units"]),
        } for r in rows]

    def _top_staff(self, opt, date_from, date_to):
        """Cashier leaderboard. Ranked on ATV, not on net.

        Net sales per cashier mostly measures how many hours they were rostered
        and how busy their store is. ATV is the part they influence, so it is
        what the list is ordered by — with net shown alongside for context.

        The ``HAVING`` is safe because X24DN repeats the cashier on every line of
        a transaction: whoever owns the transaction also owns its first line, so
        no cashier can carry sales without carrying the transactions behind them.
        """
        rows = self._query(
            SQL(
                """
                SELECT r.staff_name AS staff, MAX(r.staff_code) AS code, %s
                  FROM %s r
                 WHERE %s AND r.staff_name IS NOT NULL
              GROUP BY r.staff_name
                HAVING SUM(r.order_count) > 0
                """,
                SQL(self._MEASURES),
                SQL.identifier(self._table),
                self._where(opt, date_from, date_to),
            )
        )
        out = [{
            "name": r["staff"],
            "code": r["code"] or "",
            "net": r["net"],
            "transactions": r["transactions"],
            "units": r["units"],
            "atv": self._ratio(r["net"], r["transactions"]),
            "upt": self._ratio(r["units"], r["transactions"]),
        } for r in rows]
        out.sort(key=lambda s: s["atv"], reverse=True)
        return out[:TOP_N]

    def _top_promos(self, opt, date_from, date_to):
        rows = self._query(
            SQL(
                """
                SELECT r.discount_code AS code, MAX(r.discount_type) AS promo_type, %s
                  FROM %s r
                 WHERE %s AND r.discount_code IS NOT NULL
              GROUP BY r.discount_code
              ORDER BY SUM(r.discount_amount) DESC
                 LIMIT %s
                """,
                SQL(self._MEASURES),
                SQL.identifier(self._table),
                self._where(opt, date_from, date_to),
                TOP_N,
            )
        )
        return [{
            "code": r["code"],
            "type": r["promo_type"] or "",
            "discount": r["discount"],
            "net": r["net"],
            "units": r["units"],
            # How much of the ticket the promo gave away.
            "depth_pct": self._ratio(r["discount"], r["gross"]) * 100.0,
        } for r in rows]

    def _channel_mix(self, opt, date_from, date_to):
        """Store vs omni, and member vs walk-in — the two splits management reads together."""
        rows = self._query(
            SQL(
                """
                SELECT r.is_omni AS omni, r.is_member AS member, %s
                  FROM %s r
                 WHERE %s
              GROUP BY r.is_omni, r.is_member
                """,
                SQL(self._MEASURES),
                SQL.identifier(self._table),
                self._where(opt, date_from, date_to),
            )
        )
        channel = {"store": 0.0, "omni": 0.0}
        member = {"member": 0.0, "walk_in": 0.0}
        for r in rows:
            channel["omni" if r["omni"] else "store"] += r["net"]
            member["member" if r["member"] else "walk_in"] += r["net"]
        return {"channel": channel, "member": member}

    def _filter_choices(self, opt):
        """Stores and revenue buckets offered in the filter bar.

        Sourced from POS configs rather than from all warehouses, so a
        distribution centre that never rang a sale does not appear as a store.
        """
        configs = self.env["pos.config"].with_context(active_test=False).search(
            [("company_id", "in", opt["company_ids"])]
        )
        warehouses = configs.mapped("warehouse_id")
        roots = self.env["product.category"].search([("parent_id", "=", False)])
        return {
            "warehouses": [{"id": w.id, "name": w.display_name} for w in warehouses.sorted("name")],
            "categories": [{"id": c.id, "name": c.display_name} for c in roots],
        }
