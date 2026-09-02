"""Freshness comes from the pipeline, not from a clock.

Master prompt §4 and contract 03 §3 both say the same thing in different words: the dashboard's
"last refreshed at" must be read from real pipeline metadata. A client clock would show a freshness
indicator that is always green, because the browser's clock always advances -- including while the
pipeline is dead, which is precisely when the indicator matters.

The decisive test is therefore not "does it advance" but **"does it stop"**. Anything that ticks on
its own passes the first and fails the second.
"""

from __future__ import annotations

import time

import pytest

from conftest import wait_for
from helpers import db, env, loader

pytestmark = [pytest.mark.live]


def _tenant():
    return env.env("CDC_TENANT_SLUG", env.env("ODOO_DB_NAME", "bct"))


def test_mart_sla_table_matches_adr_0001(warehouse_up, evidence):
    """`warehouse.mart_sla` is ADR 0001's freshness table as data. It must still say the same thing."""
    grid = db.grid(
        db.warehouse_admin(),
        "SELECT mart_name, sla_seconds, on_breach FROM warehouse.mart_sla ORDER BY sla_seconds, 1;",
    )
    evidence.add("warehouse.mart_sla", grid)
    rows = dict(
        (r[0], (int(r[1]), r[2])) for r in db.query(
            db.warehouse_admin(), "SELECT mart_name, sla_seconds, on_breach FROM warehouse.mart_sla;"
        )
    )
    # The four the ADR states as numbers with a defined consequence.
    expected = {
        "mart_ppob_transaction": (60, "page"),
        "mart_stock_position": (300, "alert"),
        "mart_sales_daily": (300, "alert"),
        "mart_revenue_daily": (900, "alert"),
        "mart_account_move_line": (3600, "alert"),
    }
    problems = []
    for mart, (seconds, breach) in expected.items():
        if mart not in rows:
            problems.append("%s is missing from warehouse.mart_sla" % mart)
        elif rows[mart] != (seconds, breach):
            problems.append("%s is %r but ADR 0001 says %r" % (mart, rows[mart], (seconds, breach)))
    assert not problems, problems


def test_a_mart_with_no_pipeline_state_reports_stale(warehouse_up, evidence):
    """"A mart with no pipeline_state row reports is_stale = true. Never 'fresh' by default.\""""
    grid = db.grid(
        db.warehouse_admin(),
        "SELECT mart_name, tenant_id, sla_seconds, age_seconds, is_stale "
        "FROM warehouse.mart_freshness ORDER BY mart_name, tenant_id LIMIT 40;",
    )
    evidence.add("warehouse.mart_freshness", grid)
    orphans = db.query(
        db.warehouse_admin(),
        "SELECT mart_name, tenant_id, is_stale FROM warehouse.mart_freshness "
        "WHERE last_refreshed_at IS NULL;",
    )
    evidence.add(
        "marts with no pipeline_state row",
        "\n".join("%s/%s is_stale=%s" % r for r in orphans) or "none",
    )
    not_stale = [r for r in orphans if r[2] != "t"]
    assert not not_stale, (
        "these marts have no pipeline state yet report is_stale=false, i.e. they default to fresh: %r"
        % (not_stale,)
    )


def test_freshness_stops_advancing_when_the_pipeline_stops(
    warehouse_up, cdc_running, evidence
):
    """The whole point. Stop the loader; `last_success_at` must freeze.

    Destructive in the mild sense that it stops and restarts the loader. It restarts it in a
    `finally`, and asserts the restart worked, so a failure here does not leave the pipeline down.
    """
    tenant = _tenant()
    admin = db.warehouse_admin()
    sql = (
        "SELECT max(last_success_at)::text FROM warehouse.pipeline_state WHERE tenant_id = '%s';"
        % tenant
    )

    # 1. While running, it advances.
    first = db.scalar(admin, sql)
    advanced, elapsed = wait_for(lambda: db.scalar(admin, sql) != first, 60, 2.0)
    later = db.scalar(admin, sql)
    evidence.add(
        "RUNNING: last_success_at advances",
        "t0 %s\nt1 %s\nadvanced within %.1fs: %s" % (first, later, elapsed, bool(advanced)),
    )
    assert advanced, (
        "last_success_at did not advance in %ds with the loader running, so the 'stops advancing' "
        "half of this test cannot distinguish a stopped pipeline from a broken heartbeat." % 60
    )

    stopped_at = None
    try:
        loader.stop_main()
        time.sleep(3)
        stopped_at = db.scalar(admin, sql)
        time.sleep(35)  # comfortably longer than the heartbeat interval
        after_wait = db.scalar(admin, sql)
        evidence.add(
            "STOPPED: last_success_at freezes",
            "at stop      %s\n35s later    %s\nfrozen: %s"
            % (stopped_at, after_wait, stopped_at == after_wait),
        )
        assert stopped_at == after_wait, (
            "last_success_at moved from %s to %s while the loader was stopped. It is therefore not "
            "reading pipeline reality, and meta.is_stale would never go true."
            % (stopped_at, after_wait)
        )

        age = db.scalar(
            admin,
            "SELECT round(extract(epoch FROM now() - max(last_success_at))) FROM "
            "warehouse.pipeline_state WHERE tenant_id = '%s';" % tenant,
        )
        evidence.add("age of the freshest pipeline_state row while stopped", "%s seconds" % age)
        assert float(age) >= 30
    finally:
        result = loader.start_main()
        evidence.add(
            "loader restarted",
            "rc=%d %s" % (result.returncode, (result.stdout or result.stderr).strip()[-200:]),
        )
        assert result.returncode == 0, "failed to restart the loader; the stack is left degraded"
        recovered, seconds = wait_for(
            lambda: db.scalar(admin, sql) != stopped_at, 90, 2.0
        )
        evidence.add(
            "RESTARTED: last_success_at resumes",
            "advanced again within %.1fs: %s" % (seconds, bool(recovered)),
        )
        assert recovered, "the loader restarted but last_success_at never moved again"


def test_semantic_api_serves_freshness_from_pipeline_state(semantic_up, evidence):
    """`meta.last_refreshed_at` must equal what `warehouse.mart_freshness` says, not `now()`."""
    from helpers import tokens, web

    token = tokens.valid(tokens.claims(tenant=_tenant(), all_ou=True))
    response = web.request(
        web.semantic_url("/v1/query"), method="POST",
        payload={"metric": "revenue_net", "dimensions": ["date_day"],
                 "filters": {"date_range": ["2026-01-01", "2026-12-31"]}, "limit": 1},
        headers={"Authorization": "Bearer %s" % token},
    )
    assert response.status == 200, response.body[:300]
    meta = response.json()["meta"]
    evidence.add("meta from POST /v1/query", str(meta))

    from_db = db.query(
        db.warehouse_admin(),
        "SELECT last_refreshed_at::text, is_stale FROM warehouse.mart_freshness "
        "WHERE mart_name = '%s' AND tenant_id = '%s';" % (meta["source_model"], _tenant()),
    )
    evidence.add("warehouse.mart_freshness for that mart", str(from_db))
    assert from_db, "no mart_freshness row for %s" % meta["source_model"]
    assert meta["last_refreshed_at"] is not None

    # Compare instants, not spellings. The API serialises ISO-8601 with a `T`; psql prints a space.
    # A string comparison here fails on the separator while the two values are the same moment --
    # which would be a test reporting a defect that does not exist.
    def instant(value):
        return str(value).replace("T", " ")[:26].rstrip("+0 ")

    evidence.add(
        "normalised for comparison",
        "api %s\ndb  %s" % (instant(meta["last_refreshed_at"]), instant(from_db[0][0])),
    )
    assert instant(meta["last_refreshed_at"]) == instant(from_db[0][0]), (
        "the API's last_refreshed_at (%s) does not match warehouse.mart_freshness (%s)"
        % (meta["last_refreshed_at"], from_db[0][0])
    )
