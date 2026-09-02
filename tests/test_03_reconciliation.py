"""Reconciliation: the warehouse's totals must equal Odoo's.

Master prompt §3.4 and ADR 0001's "Risks accepted" both name the same danger: a schema change or a
decoding gap makes the warehouse *drift*, and drift is silent. Nothing errors. The dashboard keeps
answering, with numbers that are quietly wrong. Reconciliation is the only thing that turns that
into a red test.

Three families of check, each a different kind of wrong:

* **Row-level presence** -- per replicated table, the number of live keys in the landing zone equals
  the number of rows in Odoo. Catches missed inserts, missed deletes and dropped tables.
* **Monetary totals per day per tenant** -- catches a column that replicates but carries a wrong
  value, which a row count cannot see.
* **Accounting identity: debit == credit** -- an internal invariant of the warehouse alone. It holds
  in Odoo by construction, so if it fails in the warehouse the failure is definitely ours.

Everything compares against the **live projection** of `raw.*` (contract 05: newest row per key,
tombstones excluded), because that is what a mart is built from. Comparing raw row counts would
compare a history to a state and would be wrong by design.

Convergence, not instantaneity: the pipeline is asynchronous, so a mismatch is only a failure if it
*persists*. Each check retries until the ADR's strictest mart SLA (60 s) has elapsed and reports the
last observed pair of numbers on failure.
"""

from __future__ import annotations

import pytest

from conftest import wait_for
from helpers import db, env, raw

pytestmark = [pytest.mark.live]

CONVERGENCE_SECONDS = 60

#: Every table classified in `warehouse.column_policy` is replicated, so the row-count check is
#: derived from the policy rather than hardcoded -- exactly as the loader derives its table list.
POSTED_CUSTOMER_INVOICES = "state = 'posted' AND move_type IN ('out_invoice','out_refund')"


def _tenant():
    return env.env("CDC_TENANT_SLUG", env.env("ODOO_DB_NAME", "bct"))


def _converge(fn, evidence, label):
    """Retry until the two sides agree, then report both numbers either way."""
    state = {}

    def attempt():
        odoo_value, warehouse_value = fn()
        state["odoo"] = odoo_value
        state["warehouse"] = warehouse_value
        return odoo_value == warehouse_value

    ok, elapsed = wait_for(attempt, CONVERGENCE_SECONDS, 2.0)
    evidence.add(
        label,
        "odoo      = %s\nwarehouse = %s\nagreed after %.1fs (budget %ds): %s"
        % (state.get("odoo"), state.get("warehouse"), elapsed, CONVERGENCE_SECONDS, ok),
    )
    return ok, state


def test_row_counts_reconcile_per_table(warehouse_up, oltp_up, cdc_warehouse, evidence):
    tenant = _tenant()
    odoo = db.oltp_odoo()
    tables = [
        r[0] for r in db.query(
            cdc_warehouse, "SELECT DISTINCT source_table FROM warehouse.column_policy ORDER BY 1;"
        )
    ]
    assert tables, "warehouse.column_policy is empty; nothing is classified, so nothing replicates"
    evidence.add("replicated tables (derived from warehouse.column_policy)", ", ".join(tables))

    report, mismatches = [], []
    for table in tables:
        def pair(table=table):
            in_odoo = int(db.scalar(odoo, "SELECT count(*) FROM %s;" % table))
            in_warehouse = int(db.scalar(cdc_warehouse, raw.latest_count(table, tenant)))
            return in_odoo, in_warehouse

        ok, state = _converge(pair, evidence, "row count :: %s" % table)
        report.append("%-20s odoo=%-8s warehouse=%-8s %s"
                      % (table, state["odoo"], state["warehouse"], "OK" if ok else "MISMATCH"))
        if not ok:
            mismatches.append(
                "%s: odoo=%s warehouse=%s (delta %s)"
                % (table, state["odoo"], state["warehouse"], state["warehouse"] - state["odoo"])
            )
    evidence.add("row-count reconciliation, all tables", "\n".join(report))
    assert not mismatches, "\n".join(mismatches)


def test_revenue_reconciles_per_day_per_tenant(warehouse_up, oltp_up, cdc_warehouse, evidence):
    """Invoiced revenue, day by day. A per-day comparison localises the drift to a date."""
    tenant = _tenant()
    odoo = db.oltp_odoo()

    odoo_sql = (
        "SELECT invoice_date::text, sum(amount_total)::numeric(18,2)::text "
        "FROM account_move WHERE %s AND invoice_date IS NOT NULL "
        "GROUP BY 1 ORDER BY 1;" % POSTED_CUSTOMER_INVOICES
    )
    warehouse_sql = (
        "SELECT invoice_date::text, sum(amount_total)::numeric(18,2)::text FROM (%s) live "
        "WHERE %s AND invoice_date IS NOT NULL GROUP BY 1 ORDER BY 1;"
        % (raw.latest("account_move", tenant, "state, move_type, invoice_date, amount_total"),
           POSTED_CUSTOMER_INVOICES)
    )

    ok, state = _converge(
        lambda: (db.query(odoo, odoo_sql), db.query(cdc_warehouse, warehouse_sql)),
        evidence,
        "revenue per day (posted customer invoices and credit notes)",
    )
    evidence.add(
        "revenue by day -- odoo | warehouse",
        _side_by_side(state["odoo"], state["warehouse"]),
    )
    assert ok, "revenue per day does not reconcile; see the side-by-side above"
    assert state["odoo"], "no posted customer invoices in Odoo at all: this check proved nothing"
    assert len(state["odoo"]) >= 2, (
        "revenue reconciles across only %d day(s); the per-day claim needs more than one day of "
        "demo data to mean anything" % len(state["odoo"])
    )


def test_debit_equals_credit_in_the_warehouse(warehouse_up, cdc_warehouse, evidence):
    """The accounting identity, asserted on warehouse data alone.

    Odoo cannot post an unbalanced move, so this is a statement about *our* replication: if the two
    sides differ here, a journal line was lost, duplicated, or landed with a mangled amount.
    """
    tenant = _tenant()
    sql = (
        "SELECT sum(debit)::numeric(18,2)::text, sum(credit)::numeric(18,2)::text, "
        "(sum(debit) - sum(credit))::numeric(18,2)::text, count(*)::text FROM (%s) live;"
        % raw.latest("account_move_line", tenant, "debit, credit")
    )
    grid = db.grid(cdc_warehouse, sql.replace(";", " ;"))
    evidence.add("debit / credit over the live projection of raw.account_move_line", grid)
    debit, credit, delta, count = db.query(cdc_warehouse, sql)[0]
    assert int(count) > 0, "raw.account_move_line has no live rows; the identity is vacuous"
    assert debit == credit, (
        "the warehouse's journal does not balance: debit=%s credit=%s delta=%s over %s lines"
        % (debit, credit, delta, count)
    )


def test_debit_credit_matches_odoo(warehouse_up, oltp_up, cdc_warehouse, evidence):
    tenant = _tenant()
    odoo = db.oltp_odoo()
    ok, state = _converge(
        lambda: (
            db.query(odoo, "SELECT sum(debit)::numeric(18,2)::text, "
                           "sum(credit)::numeric(18,2)::text FROM account_move_line;"),
            db.query(cdc_warehouse,
                     "SELECT sum(debit)::numeric(18,2)::text, sum(credit)::numeric(18,2)::text "
                     "FROM (%s) live;" % raw.latest("account_move_line", tenant, "debit, credit")),
        ),
        evidence,
        "total debit and credit, odoo vs warehouse",
    )
    assert ok


def test_stock_quantity_reconciles(warehouse_up, oltp_up, cdc_warehouse, evidence):
    tenant = _tenant()
    odoo = db.oltp_odoo()
    ok, state = _converge(
        lambda: (
            db.query(odoo, "SELECT sum(product_uom_qty)::numeric(18,4)::text, count(*)::text "
                           "FROM stock_move;"),
            db.query(cdc_warehouse,
                     "SELECT sum(product_uom_qty)::numeric(18,4)::text, count(*)::text FROM (%s) live;"
                     % raw.latest("stock_move", tenant, "product_uom_qty")),
        ),
        evidence,
        "stock_move quantity and row count",
    )
    assert ok


def test_reconciliation_covers_more_than_one_operating_unit(warehouse_up, cdc_warehouse, evidence):
    """A single-OU dataset would let an OU-scoping bug pass every check above."""
    tenant = _tenant()
    grid = db.grid(
        cdc_warehouse,
        "SELECT operating_unit_id, count(*) FROM (%s) live GROUP BY 1 ORDER BY 1;"
        % raw.latest("account_move", tenant, "operating_unit_id"),
    )
    evidence.add("account_move rows per operating unit, in the warehouse", grid)
    rows = db.query(
        cdc_warehouse,
        "SELECT count(DISTINCT operating_unit_id) FROM (%s) live WHERE operating_unit_id IS NOT NULL;"
        % raw.latest("account_move", tenant, "operating_unit_id"),
    )
    assert int(rows[0][0]) >= 2, (
        "only %s operating unit(s) present in the warehouse; OU-scoping cannot be tested against "
        "this dataset" % rows[0][0]
    )


def _side_by_side(left, right):
    left_map = dict(left or [])
    right_map = dict(right or [])
    keys = sorted(set(left_map) | set(right_map))
    lines = ["%-12s %18s %18s  %s" % ("day", "odoo", "warehouse", "")]
    for key in keys:
        a, b = left_map.get(key), right_map.get(key)
        lines.append("%-12s %18s %18s  %s" % (key, a, b, "" if a == b else "<-- MISMATCH"))
    return "\n".join(lines)
