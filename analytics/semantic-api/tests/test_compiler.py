"""The compiler refuses everything the contract does not declare, before any SQL exists.

Acceptance criteria 6 and 7: an undeclared metric, dimension or filter returns 400, and a
raw-SQL-looking payload is rejected with no code path that executes caller-supplied SQL.

The second is the interesting one, and it is a *structural* claim rather than an escaping claim.
Every identifier the compiler emits is looked up in the metric definition first; a caller string
never becomes an identifier. So an injection payload does not get escaped and executed safely — it
fails the allow-list before a statement is built at all. The tests below assert that distinction by
checking the *error*, not just the absence of damage.
"""

from __future__ import annotations

import pytest

from app.compiler import UNASSIGNED_OPERATING_UNIT_ID, QueryRejected, compile_query
from app.registry import Metric

METRIC = Metric({
    "name": "revenue_net",
    "label": "Pendapatan Neto",
    "grain": ["date_day", "tenant_id", "operating_unit_id"],
    "dimensions": ["date_day", "date_month", "tenant_id", "operating_unit_id", "product_key"],
    "filters": {
        "date_range": {"type": "daterange", "required": True, "column": "date_day"},
        "operating_unit_id": {"type": "int[]", "required": False},
        "product_key": {"type": "string[]", "required": False},
    },
    "type": "decimal",
    "unit": "IDR",
    "aggregation": "sum",
    "source_model": "mart_revenue_daily",
    "measure": "revenue_net",
    "refresh_sla_seconds": 900,
    "pdp_class": "internal",
})

VALID_FILTERS = {"date_range": ["2026-01-01", "2026-08-31"]}


def compile_ok(**kwargs):
    params = dict(
        metric=METRIC, dimensions=["date_month"], filters=dict(VALID_FILTERS),
        order_by=None, limit=100, tenant_id="bct", allowed_ou=[], all_ou=True,
    )
    params.update(kwargs)
    return compile_query(**params)


def test_a_valid_query_compiles():
    statement, params = compile_ok()
    assert "bct" in params


def test_undeclared_dimension_is_rejected():
    with pytest.raises(QueryRejected) as exc:
        compile_ok(dimensions=["not_a_dimension"])
    assert exc.value.field == "dimensions"


def test_undeclared_filter_is_rejected():
    with pytest.raises(QueryRejected) as exc:
        compile_ok(filters={**VALID_FILTERS, "secret_column": "x"})
    assert exc.value.field == "filters"


def test_required_filter_missing_is_rejected():
    with pytest.raises(QueryRejected) as exc:
        compile_ok(filters={})
    assert "required" in exc.value.detail


@pytest.mark.parametrize("payload", [
    "1; DROP TABLE marts.mart_revenue_daily; --",
    "date_day) UNION SELECT password FROM res_users --",
    "*",
    "date_day/**/OR/**/1=1",
    "'; SELECT pg_sleep(10); --",
])
def test_raw_sql_in_a_dimension_is_rejected_by_the_allow_list(payload):
    """Not escaped and executed safely -- rejected before any SQL is built."""
    with pytest.raises(QueryRejected) as exc:
        compile_ok(dimensions=[payload])
    assert exc.value.field == "dimensions"
    assert "not declared" in exc.value.detail


@pytest.mark.parametrize("payload", [
    "1; DROP TABLE x; --",
    "value; DELETE FROM marts.mart_revenue_daily",
])
def test_raw_sql_in_order_by_is_rejected(payload):
    with pytest.raises(QueryRejected) as exc:
        compile_ok(order_by=payload)
    assert exc.value.field == "order_by"


def test_order_by_must_be_value_or_a_requested_dimension():
    # Ordering by a legal dimension that was NOT requested would need it in GROUP BY; allowing an
    # arbitrary declared name here is the subtler version of the same hole.
    with pytest.raises(QueryRejected):
        compile_ok(dimensions=["date_month"], order_by="product_key")
    compile_ok(dimensions=["date_month"], order_by="-date_month")
    compile_ok(dimensions=["date_month"], order_by="value")


def test_filter_values_are_type_checked():
    with pytest.raises(QueryRejected):
        compile_ok(filters={**VALID_FILTERS, "operating_unit_id": ["not-an-int"]})
    with pytest.raises(QueryRejected):
        compile_ok(filters={**VALID_FILTERS, "product_key": [123]})
    with pytest.raises(QueryRejected):
        compile_ok(filters={"date_range": ["not-a-date", "2026-08-31"]})
    with pytest.raises(QueryRejected):
        compile_ok(filters={"date_range": ["2026-09-01", "2026-01-01"]})


def test_tenant_is_always_a_bound_parameter():
    """Contract 02: bound as a parameter *and* set as the RLS variable. This is the parameter half."""
    statement, params = compile_ok(tenant_id="acme")
    assert "acme" in params
    # It must not have been interpolated into the SQL text.
    assert "acme" not in str(statement)


def test_all_ou_true_emits_no_operating_unit_predicate():
    statement, _ = compile_ok(all_ou=True, allowed_ou=[])
    assert "operating_unit_id" not in str(statement)


def test_all_ou_false_with_ids_restricts_to_them():
    statement, params = compile_ok(all_ou=False, allowed_ou=[1, 4])
    assert "operating_unit_id" in str(statement)
    assert [1, 4] in params


def test_empty_allowed_ou_selects_the_unassigned_member_not_null_and_not_everything():
    """Ruling a0fbb88, plus the trap that only showed up against the real marts.

    Two separate mistakes are pinned here:

    * Reading ``[]`` as "everything" would give a user more in the dashboard than in Odoo, whose
      record rules fail closed on an empty entitlement.
    * Reading it as ``IS NULL`` -- the first implementation, reasoning by analogy with Odoo --
      matches NOTHING, because the warehouse represents unassigned as the explicit dimension member
      ``-1``. Verified against live marts: zero rows carry a NULL operating_unit_id.
    """
    statement, params = compile_ok(all_ou=False, allowed_ou=[])
    text = str(statement)
    assert "operating_unit_id" in text
    assert "IS NULL" not in text, "unassigned is -1 in the warehouse, never SQL NULL"
    assert UNASSIGNED_OPERATING_UNIT_ID in params


def test_limit_is_capped_rather_than_rejected():
    _, params = compile_ok(limit=999999, max_limit=500)
    assert params[-1] == 500


def test_limit_must_be_a_positive_integer():
    for bad in (0, -1, "10", 1.5, True):
        with pytest.raises(QueryRejected):
            compile_ok(limit=bad)


# -- ratio and period_growth -------------------------------------------------------------------

RATIO_METRIC = Metric({
    "name": "ppob_success_rate",
    "label": "Success rate",
    "grain": ["date_day", "tenant_id"],
    "dimensions": ["date_day", "tenant_id", "biller_code"],
    "filters": {"date_range": {"type": "daterange", "required": True, "column": "date_day"}},
    "type": "percent",
    "aggregation": "ratio",
    "numerator": {"measure": "is_success", "agg": "count_true"},
    "denominator": {"measure": "ppob_transaction_id", "agg": "count"},
    "source_model": "fct_ppob_transaction",
    "refresh_sla_seconds": 60,
    "pdp_class": "internal",
})

GROWTH_METRIC = Metric({
    "name": "revenue_mom_growth",
    "label": "MoM growth",
    "grain": ["date_month", "tenant_id"],
    "dimensions": ["date_month", "tenant_id", "revenue_channel"],
    "derived_dimensions": {"date_month": {"from": "date_day", "grain": "month"}},
    "filters": {"date_range": {"type": "daterange", "required": True, "column": "date_day"}},
    "type": "percent",
    "aggregation": "period_growth",
    "measure": "revenue_net",
    "growth_over": "date_month",
    "channel_note": "summed across channels deliberately",
    "source_model": "mart_revenue_daily",
    "refresh_sla_seconds": 900,
    "pdp_class": "internal",
})


def compile_metric(metric, **kwargs):
    params = dict(
        metric=metric, dimensions=["tenant_id"], filters=dict(VALID_FILTERS),
        order_by=None, limit=100, tenant_id="bct", allowed_ou=[], all_ou=True,
    )
    params.update(kwargs)
    return compile_query(**params)


def test_ratio_uses_count_true_not_sum_of_a_boolean():
    """SUM(boolean) is invalid in Postgres and SUM(col::int) silently truncates a numeric column."""
    statement, _ = compile_metric(RATIO_METRIC)
    text = str(statement)
    assert "COUNT(*) FILTER (WHERE" in text
    assert "is_success" in text


def test_ratio_guards_division_by_zero_with_nullif():
    """An empty denominator must yield NULL, not 0.

    "No rate" and "a rate of zero" are different statements, and a chart plotting 0 for an empty
    denominator asserts something the data does not say.
    """
    statement, _ = compile_metric(RATIO_METRIC)
    assert "NULLIF(" in str(statement)


def test_period_growth_emits_a_lag_window_over_the_declared_dimension():
    # str() on a psycopg2 Composed renders its REPR, not SQL, so the fragments are asserted
    # individually rather than as one contiguous string. Found by this test failing on its own
    # first run, which is the cheapest possible place to learn it.
    statement, _ = compile_metric(GROWTH_METRIC, dimensions=["date_month"])
    text = str(statement)
    assert "lag(" in text
    assert "OVER (ORDER BY" in text
    assert "SUM(" in text
    assert "revenue_net" in text
    assert "date_trunc(" in text  # date_month is derived: the window orders by the expression


def test_period_growth_without_its_time_dimension_is_rejected():
    """Without the growth dimension every row is its own group and every value would be NULL."""
    with pytest.raises(QueryRejected) as exc:
        compile_metric(GROWTH_METRIC, dimensions=["tenant_id"])
    assert exc.value.field == "dimensions"
    assert "growth over" in exc.value.detail


def test_neither_new_aggregation_lets_a_caller_string_into_sql():
    """The allow-list still holds: numerator/denominator/growth_over come from the metric file."""
    for metric in (RATIO_METRIC, GROWTH_METRIC):
        with pytest.raises(QueryRejected):
            compile_metric(metric, dimensions=["1; DROP TABLE marts.x; --"])
