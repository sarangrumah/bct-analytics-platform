# -*- coding: utf-8 -*-
{
    "name": "the apparel brand Sales Dashboard",
    "version": "19.0.1.0.0",
    "summary": "Interactive retail sales dashboard, analysis view and store targets "
    "over the imported X24DN POS data",
    "description": """
the apparel brand Sales Dashboard
======================

the apparel brand sells through stores, not through Odoo: X24DN lands the day's retail
sales as ``pos.order`` / ``pos.order.line`` and ``custom_retail_import_pos``
keeps the source workbook's own net, tax, discount, cashier, member and promo
columns on those records. Everything management asks about a trading day is
therefore already in the database — it is just spread over five tables and only
reachable one order at a time.

This module turns that into decisions.

**1. Sales Analysis (``retail.sales.report``).** A read-only SQL view, one row
per POS line, that resolves in a single pass what otherwise needs five joins:
the store (``stock.warehouse``) and its Operating-Unit analytic, the product and
its COA revenue root category, the cashier, the member type, the promo code, the
omnichannel flag and the return flag. Amounts come from the source workbook
(``ri_src_net`` / ``ri_src_tax`` / ``ri_src_discount``) and fall back to Odoo's
own subtotals for orders that were not imported, so a mixed database still ties
out. Ships list / pivot / graph / search views, so any question that is a
group-by is answerable without code.

Two details make the view honest rather than merely convenient:

* ``date`` is the **Jakarta** trading date, not the stored UTC timestamp. The
  importer stamps orders at 12:00 UTC, so a naive cast would still land on the
  right day — but a live POS order at 23:30 WIB would not.
* ``order_count`` is 1 on exactly one line per order (the lowest line id, via a
  window function), so *transactions* can be summed in a pivot instead of being
  a number only the dashboard knows how to compute.

**2. Store Targets (``retail.sales.target``).** A monthly net-sales target per
store, optionally with a unit and transaction target. Targets are **prorated by
day overlap**, so a month-to-date view is compared against a month-to-date
target rather than against the whole month — the comparison every store report
gets wrong. Overlapping periods for the same store are rejected at the database
level.

**3. Sales Command Centre.** An OWL client action that reads the whole dashboard
in one RPC (``retail.sales.report.get_dashboard``) instead of a request per tile:

* nine KPIs — net sales, transactions, units, ATV, UPT, ASP, discount depth,
  return rate and member share — each carrying its own comparison delta;
* a daily trend line overlaying the comparison period and the prorated target
  pace;
* a store leaderboard ranked by attainment, a category-mix donut over the COA
  revenue roots, a day-of-week profile, a trading-day calendar heatmap, and top
  product / cashier / promo tables;
* comparison against the previous period **or** the same period last year;
* filters for date range, store, channel (store vs omni) and returns.

Every chart element and every table row is a drill-through: clicking one opens
the Sales Analysis view with the corresponding domain already applied, so the
dashboard is an entry point into the data rather than a dead end. Charts are
inline SVG — no external library, nothing to load from a CDN.

TENANT-SCOPED: install only on the apparel brand tenant databases.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Tenants/Retail",
    "license": "LGPL-3",
    "depends": [
        "point_of_sale",
        "stock",
        "analytic",
        "custom_retail_import_pos",
        "custom_retail_localization",
    ],
    "capability_tags": ["retail", "pos", "analytics", "dashboard", "retail"],
    "data": [
        "security/retail_sales_security.xml",
        "security/ir.model.access.csv",
        "views/retail_sales_report_views.xml",
        "views/retail_sales_target_views.xml",
        "views/retail_sales_dashboard_views.xml",
        "views/retail_sales_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "custom_retail_sales_dashboard/static/src/js/sales_dashboard/sales_dashboard.js",
            "custom_retail_sales_dashboard/static/src/js/sales_dashboard/sales_dashboard.xml",
            "custom_retail_sales_dashboard/static/src/js/sales_dashboard/sales_dashboard.scss",
        ],
    },
    "installable": True,
    "auto_install": False,
    "application": True,
}
