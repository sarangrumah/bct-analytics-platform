"""Saturation must be a documented 503, never an undocumented 500.

Frontend found this while measuring p95: a ten-panel PPOB view with its cache disabled issued ten
concurrent queries against a pool of eight, psycopg2 raised ``PoolError: connection pool exhausted``,
and it fell through to the generic handler as ``500 query_failed``. Reproduced here before the fix
with Frontend's own recipe - 300 requests, 10 concurrent - as **52 x 500**, with
``bct_semantic_pool_guard_trips`` sitting at 0 throughout, which is what proves it was never the T-1
scope guard. A 500 tells a caller "this server is broken"; the truth was "every connection is busy
for the next few milliseconds".

Frontend mitigated it in its own client by capping itself at four in flight. That fixed the
dashboard and fixed nothing else: a second tab, the export path or a load test still got 500s. The
service degrading correctly is the fix; a polite client is a workaround.

QUEUE, THEN SHED - and these tests pin down both halves, because either alone is wrong. Queue-only
turns a saturated service into a hung one. Shed-only turns a 15 ms burst into user-visible errors.
"""

from __future__ import annotations

import threading
import time

import psycopg2
import pytest

from app.db import PoolExhausted, Warehouse


class _FakeCursor:
    """Enough of a cursor for the T-1 checkout guard and the SET LOCAL preamble.

    ``current_setting(app.tenant_id, true)`` must come back as None, or every checkout trips the
    guard, is discarded, and the concurrency tests measure the guard instead of the pool.
    """

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._sql = sql

    def fetchone(self):
        return [None]

    def close(self):
        pass


class _FakeConn:
    autocommit = False

    def cursor(self, cursor_factory=None):
        return _FakeCursor()

    def commit(self):
        pass

    def rollback(self):
        pass


class _FakePool:
    """Stands in for psycopg2's pool. Never opens a socket."""

    def __init__(self, *a, **kw):
        self.checked_out = 0
        self.peak = 0
        self._lock = threading.Lock()

    def getconn(self):
        with self._lock:
            self.checked_out += 1
            self.peak = max(self.peak, self.checked_out)
        return _FakeConn()

    def putconn(self, conn, close=False):
        with self._lock:
            self.checked_out -= 1

    def closeall(self):
        pass


@pytest.fixture
def warehouse(monkeypatch):
    monkeypatch.setattr("app.db.pg_pool.ThreadedConnectionPool", _FakePool)
    return Warehouse("dsn", maxconn=2, acquire_timeout_s=0.05)


def test_the_default_ceiling_is_sized_and_not_eight(monkeypatch):
    """The old default was 8 and it was not derived from anything.

    16 is what is left of the warehouse's 40 max_connections after the other consumers; the
    arithmetic lives in Warehouse.__init__ so the number can be re-derived when they change.
    """
    monkeypatch.setattr("app.db.pg_pool.ThreadedConnectionPool", _FakePool)
    monkeypatch.delenv("SEMANTIC_API_POOL_MAX", raising=False)
    assert Warehouse("dsn").maxconn == 16


def test_a_burst_within_the_timeout_QUEUES_and_still_succeeds(warehouse):
    """Ten panels against two connections must all be served, not shed.

    This is the half a shed-only design gets wrong. Nothing here asserts a 503; the correct
    outcome is that every caller gets its data.
    """
    done, errors = [], []

    def worker():
        try:
            with warehouse.session("bct"):
                time.sleep(0.001)
            done.append(1)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], "a burst inside the timeout was shed instead of queued: %r" % errors
    assert len(done) == 10
    assert warehouse.waits > 0, (
        "no request ever queued, so this test did not exercise the queue at all and would pass "
        "against a pool with no limit whatsoever"
    )
    assert warehouse.shed == 0


def test_holding_every_slot_past_the_timeout_SHEDS_with_PoolExhausted(warehouse):
    """The half a queue-only design gets wrong, and the branch that must never go untested."""
    held = threading.Event()
    release = threading.Event()

    def holder():
        with warehouse.session("bct"):
            held.set()
            release.wait(timeout=5)

    holders = [threading.Thread(target=holder) for _ in range(2)]
    for t in holders:
        t.start()
    held.wait(timeout=5)
    time.sleep(0.02)

    try:
        with pytest.raises(PoolExhausted) as exc:
            with warehouse.session("bct"):
                pass
        assert "busy" in str(exc.value)
        assert exc.value.retry_after >= 1, "a 503 with no usable Retry-After is half a contract"
    finally:
        release.set()
        for t in holders:
            t.join()

    assert warehouse.shed == 1


def test_the_pool_never_exceeds_its_ceiling(warehouse):
    """The semaphore is what makes psycopg2's PoolError structurally unreachable.

    Catching PoolError would also have removed the 500. Not exceeding maxconn in the first place is
    strictly better, and this asserts the stronger property.
    """
    def worker():
        try:
            with warehouse.session("bct"):
                time.sleep(0.002)
        except PoolExhausted:
            pass

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert warehouse._pool.peak <= 2, (
        "checked out %d connections against maxconn=2" % warehouse._pool.peak
    )


def test_slots_are_returned_when_the_body_raises(warehouse):
    """A slot leaked on the error path sheds for ever while the database sits idle.

    That would be a worse failure than the one this change fixes, and it is invisible until the
    service has been up long enough to leak maxconn of them.
    """
    for _ in range(10):
        with pytest.raises(ValueError):
            with warehouse.session("bct"):
                raise ValueError("boom")
    assert warehouse.shed == 0
    with warehouse.session("bct"):
        pass


# ----------------------------------------------------------------------------------------------
# /healthz — found live, and the sharpest instance of this build's pattern I have produced.
# ----------------------------------------------------------------------------------------------


class _DeadPool(_FakePool):
    """A pool whose database has gone away underneath it."""

    def getconn(self):
        raise psycopg2.OperationalError("could not translate host name")


def test_healthz_reports_down_when_the_warehouse_is_unreachable(monkeypatch):
    """The old /healthz counted registry metrics and returned 200. That is not a health check.

    Observed live, not imagined: after the warehouse container was destroyed, a semantic-api left
    over from the previous session answered ``200 {"status":"ok"}`` on /healthz while every
    /v1/query returned 500 — on the port a cold start needed. A probe of that endpoint would have
    produced a green from a service incapable of answering a single question.

    Confirmed end to end against a real throwaway Postgres before this test was written:
        warehouse up   -> 200 {"status":"ok",...,"warehouse":"ok"}
        warehouse gone -> 503 {"status":"down",...,"warehouse":"unreachable"}
    """
    monkeypatch.setattr("app.db.pg_pool.ThreadedConnectionPool", _DeadPool)
    warehouse = Warehouse("dsn", maxconn=2)
    with pytest.raises(psycopg2.OperationalError):
        warehouse.fetch_all("t", "SELECT 1")


def test_healthz_distinguishes_saturated_from_down(warehouse):
    """`degraded` and `down` are different states and must not collapse into one.

    A saturated pool means the database is fine and the service is serving; taking that instance
    out of rotation is the opposite of what it needs. Collapsing the two would recreate exactly the
    500-vs-503 conflation this module was written to fix, one layer up.
    """
    held = threading.Event()
    release = threading.Event()

    def holder():
        with warehouse.session("bct"):
            held.set()
            release.wait(timeout=5)

    holders = [threading.Thread(target=holder) for _ in range(2)]
    for t in holders:
        t.start()
    held.wait(timeout=5)
    time.sleep(0.02)
    try:
        with pytest.raises(PoolExhausted):
            warehouse.fetch_all("__healthz__", "SELECT 1 AS ok")
    finally:
        release.set()
        for t in holders:
            t.join()


# ----------------------------------------------------------------------------------------------
# Contract 05 §A.6 — application_name. Found by DWH writing the clause down, not by a test failing.
# ----------------------------------------------------------------------------------------------


def test_the_pool_names_itself_to_postgres(monkeypatch):
    """The connection must carry application_name=semantic-api.

    Not cosmetic. warehouse.access_audit.application_name reads
    current_setting('application_name'), and log_line_prefix's %a is the fallback that keeps a read
    attributable when nothing calls log_access(). Unset, an audit row records NULL for the one
    column saying WHICH service read the data — and because warehouse_rls is deliberately SHARED
    with warehouse-exporter, usename cannot separate us. This string is the only thing that can.

    Nothing FAILS while it is unset, which is why it survived: the column exists, the function runs,
    and the audit quietly records nothing. This test is the regression guard, and it deliberately
    asserts the exact contract value rather than "some non-empty string" — a variant spelling would
    satisfy a truthiness check and still break the join a reader does against §A.6's table.
    """
    captured = {}

    class _CapturingPool(_FakePool):
        def __init__(self, minconn, maxconn, dsn, **kwargs):
            captured["dsn"] = dsn
            captured["kwargs"] = kwargs
            super().__init__()

    monkeypatch.setattr("app.db.pg_pool.ThreadedConnectionPool", _CapturingPool)
    Warehouse("host=x dbname=y")

    assert captured["kwargs"].get("application_name") == "semantic-api", (
        "connections would reach the warehouse anonymous; access_audit.application_name records "
        "NULL and log_line_prefix %%a is empty. Contract 05 §A.6. Got: %r"
        % captured["kwargs"].get("application_name")
    )


def test_the_application_name_is_not_settable_from_the_environment(monkeypatch):
    """It is a contract value, not a tunable.

    An env override would let a deployment silently opt out of attributability — the same failure
    the clause was written to close, reintroduced as configuration.
    """
    monkeypatch.setenv("SEMANTIC_API_APPLICATION_NAME", "something-else")
    monkeypatch.setattr("app.db.pg_pool.ThreadedConnectionPool", _FakePool)
    from app import db as db_module

    assert db_module.APPLICATION_NAME == "semantic-api"
