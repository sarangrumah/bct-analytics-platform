"""A table can be planned, backfilled, and never replicated. This is the check that says so.

The defect this file pins down was live in the running stack: ``warehouse.column_policy`` gained
``account_account`` after ``bct_cdc_bct`` had been created, so the loader planned 16 tables against
a publication carrying 15. The backfill does **not** go through the publication -- it is a plain
``SELECT`` -- so ``raw.account_account`` held 104 rows and every one of them sat at ``_lsn 0/0``
forever. Populated and permanently stale look identical to everything downstream.

``assert_publication_excludes_secrets`` could not catch it. Its loop opens with
``if not secrets: continue`` and ``account_account`` has no ``secret``-class column, so the table
was skipped before the ``columns is None`` branch that would have reported it. That branch exists
and is correct; it is simply unreachable for the tables most likely to go missing.

Each test below is written so that it FAILS against the pre-fix code path, which is the only way to
know it works: ``test_the_secret_check_alone_cannot_see_a_missing_table`` runs the *old* check over
the *broken* input and asserts it stays silent.
"""

from __future__ import annotations

import pytest

from bct_cdc.policy import ColumnPolicy, MaskPlan, Policy
from bct_cdc.runner import (
    assert_publication_covers_plans,
    assert_publication_excludes_secrets,
)


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return self._rows


class FakeSource:
    """Stands in for the OLTP connection, returning a fixed ``pg_publication_rel`` join."""

    def __init__(self, published):
        # published: {table: [columns]} exactly as source.publication_tables() would build it
        self._rows = [
            (table, column) for table, columns in published.items() for column in columns
        ]

    def cursor(self):
        return _Cursor(self._rows)


def _plan(table, columns):
    return MaskPlan(table, {c: "none" for c in columns}, salt="x")


# The real shape of the incident: 15 published tables, 16 planned.
PUBLISHED_15 = {
    "res_partner": ["id", "name"],
    "sale_order": ["id", "amount_total"],
}
PLANS_16 = {
    "res_partner": _plan("res_partner", ["id", "name"]),
    "sale_order": _plan("sale_order", ["id", "amount_total"]),
    "account_account": _plan("account_account", ["id", "name", "account_type"]),
}


def test_a_planned_table_absent_from_the_publication_is_fatal():
    with pytest.raises(RuntimeError) as exc:
        assert_publication_covers_plans(FakeSource(PUBLISHED_15), "bct_cdc_bct", PLANS_16)
    message = str(exc.value)
    assert "account_account" in message
    # The remedy must be named: the loader cannot ALTER PUBLICATION itself.
    assert "cdc-provision.sh" in message
    # And it must say why a populated table is still broken, or the next reader "fixes" it by
    # looking at raw.account_account's row count and concluding it is fine.
    assert "stale" in message


def test_full_coverage_passes():
    published = dict(PUBLISHED_15)
    published["account_account"] = ["id", "name", "account_type"]
    # Returns None and raises nothing. Written as a bare call plus an explicit assertion because
    # `f(...) is None` on its own line evaluates to a discarded bool and asserts nothing at all --
    # ruff's B015, and exactly the "check that cannot fail" shape this file is about.
    assert assert_publication_covers_plans(
        FakeSource(published), "bct_cdc_bct", PLANS_16
    ) is None


def test_a_publication_with_no_tables_at_all_is_fatal_even_with_no_plans():
    """The empty-result rule, applied to this check itself.

    ``set(plans) - set(declared)`` is empty when ``plans`` is empty, so without this guard the
    check would report success over a publication that replicates nothing whatsoever.
    """
    with pytest.raises(RuntimeError, match="no tables at all"):
        assert_publication_covers_plans(FakeSource({}), "bct_cdc_bct", {})


def test_the_secret_check_alone_cannot_see_a_missing_table():
    """This is the red proof, kept as a test: the OLD check passes on the BROKEN input.

    If a future change makes ``assert_publication_excludes_secrets`` start catching this, that is
    good news and this test should be deleted deliberately -- not left asserting a property the
    code no longer has.
    """
    rows = [
        ColumnPolicy("account_account", c, "internal", "none", False)
        for c in ("id", "name", "account_type")
    ] + [
        ColumnPolicy("res_partner", "id", "internal", "none", False),
        ColumnPolicy("res_partner", "name", "internal", "none", False),
        ColumnPolicy("sale_order", "id", "internal", "none", False),
        ColumnPolicy("sale_order", "amount_total", "internal", "none", False),
    ]
    policy = Policy(rows)
    # Precondition, stated rather than assumed: account_account really has no secret column, which
    # is the exact reason the old check skips it.
    assert policy.secret_columns("account_account") == []
    # And the population is non-empty: the table IS planned. Without this the assertion below
    # would pass on an empty plan set for the wrong reason.
    assert "account_account" in PLANS_16

    # No exception -- the missing table is invisible to it.
    assert_publication_excludes_secrets(
        FakeSource(PUBLISHED_15), "bct_cdc_bct", policy, PLANS_16
    )

    # The new check, on the same input, is not.
    with pytest.raises(RuntimeError):
        assert_publication_covers_plans(FakeSource(PUBLISHED_15), "bct_cdc_bct", PLANS_16)


def test_the_secret_check_still_catches_a_missing_table_that_does_have_secrets():
    """The old check's ``columns is None`` branch is correct and must keep working."""
    rows = [
        ColumnPolicy("res_users", "id", "internal", "none", False),
        ColumnPolicy("res_users", "password", "secret", "drop", False),
    ]
    policy = Policy(rows)
    assert policy.secret_columns("res_users") == ["password"]
    plans = {"res_users": _plan("res_users", ["id"])}
    with pytest.raises(RuntimeError, match="not in publication"):
        assert_publication_excludes_secrets(FakeSource({}), "bct_cdc_bct", policy, plans)
