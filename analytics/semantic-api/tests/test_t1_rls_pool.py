"""Security finding T-1: prove a pooled connection cannot leak one tenant's rows to another.

The finding, restated: Postgres RLS reads a **session** variable, so a pool that hands a connection
still carrying ``app.tenant_id = 'tenant_a'`` to a request for tenant B serves A's rows to B, and
the database sees nothing wrong. There is no error to find.

These tests are deliberately built so they would **fail** against the naive implementation. The
critical one is :func:`test_pooled_connection_reused_across_tenants_does_not_leak`, which pins the
pool to **maxconn=1** so the second tenant is guaranteed to receive the *same physical connection*
the first tenant just used. Without that, a pool that happened to hand out a fresh connection would
make a broken implementation look correct.

They run against a throwaway Postgres containing a mart-shaped table with contract 05's RLS
policies reproduced verbatim, seeded with two tenants. That is deliberate: T-1 is a defect in the
*connection handling*, not in DWH's policies, so the test must exercise this repository's pooling
code. When DWH's real marts land the same tests can be pointed at them by changing one DSN.
"""

from __future__ import annotations

import os

import psycopg2
import pytest

from app.db import TENANT_SETTING, PoolGuardTripped, TenantScopeError, Warehouse

#: Superuser DSN, used ONLY to build the fixture.
ADMIN_DSN = os.environ.get("T1_ADMIN_DSN")
#: The DSN under test. Must be a role that is NOT superuser and NOT BYPASSRLS -- see
#: :func:`test_the_role_under_test_cannot_bypass_rls`, which is not optional.
RLS_DSN = os.environ.get("T1_RLS_DSN")

pytestmark = pytest.mark.skipif(
    not (ADMIN_DSN and RLS_DSN), reason="T1_ADMIN_DSN / T1_RLS_DSN not set"
)

TENANT_A = "tenant_a"
TENANT_B = "tenant_b"
RLS_ROLE = "t1_rls"


@pytest.fixture(scope="module")
def seeded():
    """Create a mart-shaped table with contract 05's policies, seeded with two tenants."""
    conn = psycopg2.connect(ADMIN_DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 't1_rls') THEN "
            "CREATE ROLE t1_rls LOGIN PASSWORD 'rlspass' NOSUPERUSER NOBYPASSRLS; END IF; END $$"
        )
        cur.execute("ALTER ROLE t1_rls NOSUPERUSER NOBYPASSRLS")
        cur.execute("CREATE SCHEMA IF NOT EXISTS marts")
        cur.execute("DROP TABLE IF EXISTS marts.fct_t1 CASCADE")
        cur.execute(
            "CREATE TABLE marts.fct_t1 ("
            " tenant_id text NOT NULL, operating_unit_id integer,"
            " date_day date NOT NULL, revenue_net numeric NOT NULL)"
        )
        cur.execute(
            "INSERT INTO marts.fct_t1 VALUES"
            " ('tenant_a', 1, '2026-01-01', 100),"
            " ('tenant_a', 1, '2026-01-02', 200),"
            " ('tenant_b', 2, '2026-01-01', 999),"
            " ('tenant_b', 2, '2026-01-02', 888)"
        )
        # Contract 05: ENABLE *and* FORCE. FORCE is what makes the owner subject to its own
        # policies -- without it, ownership silently bypasses RLS and every isolation test passes
        # while proving nothing.
        cur.execute("ALTER TABLE marts.fct_t1 ENABLE ROW LEVEL SECURITY")
        cur.execute("ALTER TABLE marts.fct_t1 FORCE ROW LEVEL SECURITY")
        cur.execute("DROP POLICY IF EXISTS p_tenant_isolation ON marts.fct_t1")
        cur.execute(
            "CREATE POLICY p_tenant_isolation ON marts.fct_t1 FOR ALL TO PUBLIC "
            "USING (tenant_id = current_setting('app.tenant_id', true))"
        )
        cur.execute("GRANT USAGE ON SCHEMA marts TO t1_rls")
        cur.execute("GRANT SELECT ON marts.fct_t1 TO t1_rls")
    conn.close()
    yield
    conn = psycopg2.connect(ADMIN_DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS marts.fct_t1 CASCADE")
    conn.close()


@pytest.fixture
def pool_of_one(seeded):
    """A pool with exactly ONE connection, so reuse across tenants is guaranteed, not hoped for."""
    warehouse = Warehouse(RLS_DSN, minconn=1, maxconn=1)
    yield warehouse
    warehouse.close()


SELECT_ALL = "SELECT tenant_id, revenue_net FROM marts.fct_t1 ORDER BY date_day"


def test_the_role_under_test_cannot_bypass_rls(seeded):
    """The most important assertion in this file, and it is about the FIXTURE, not the code.

    A superuser, or any role with BYPASSRLS, ignores row-level security entirely. Run these tests
    as such a role and every isolation assertion passes while proving absolutely nothing. That is
    not hypothetical: this project already had a CDC fixture database whose only role was
    `Superuser, Bypass RLS`, and it would have made the whole isolation suite green.

    So the properties of the identity are asserted before anything is concluded from its results.
    """
    conn = psycopg2.connect(RLS_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            user, is_super, bypasses = cur.fetchone()
    finally:
        conn.close()
    assert user == RLS_ROLE
    assert is_super is False, "the role under test is a superuser; RLS would not apply to it"
    assert bypasses is False, "the role under test has BYPASSRLS; these tests would prove nothing"


def test_the_fixture_would_actually_catch_a_leak(pool_of_one):
    """Guard the guard: if RLS were not applied, every test below would pass vacuously."""
    rows = pool_of_one.fetch_all(TENANT_A, SELECT_ALL, ())
    assert rows, "tenant_a must see its own rows, or the fixture is broken, not the isolation"
    assert len({r["tenant_id"] for r in rows}) == 1


def test_pooled_connection_reused_across_tenants_does_not_leak(pool_of_one):
    """THE T-1 TEST. One physical connection, two tenants, in sequence."""
    a_rows = pool_of_one.fetch_all(TENANT_A, SELECT_ALL, ())
    assert {r["tenant_id"] for r in a_rows} == {TENANT_A}
    assert sorted(float(r["revenue_net"]) for r in a_rows) == [100.0, 200.0]

    # Same connection object, by construction: maxconn=1.
    b_rows = pool_of_one.fetch_all(TENANT_B, SELECT_ALL, ())
    assert {r["tenant_id"] for r in b_rows} == {TENANT_B}, (
        "LEAK: tenant_b received rows scoped to another tenant from a reused pooled connection"
    )
    assert sorted(float(r["revenue_net"]) for r in b_rows) == [888.0, 999.0]

    # And back again, to catch an implementation that only resets in one direction.
    a_again = pool_of_one.fetch_all(TENANT_A, SELECT_ALL, ())
    assert {r["tenant_id"] for r in a_again} == {TENANT_A}


def test_many_alternations_on_one_connection(pool_of_one):
    """Alternate repeatedly: a leak that needs a specific ordering still shows up here."""
    for index in range(12):
        tenant = TENANT_A if index % 2 == 0 else TENANT_B
        rows = pool_of_one.fetch_all(tenant, SELECT_ALL, ())
        assert {r["tenant_id"] for r in rows} == {tenant}, "leak on iteration %d" % index


def test_scope_does_not_survive_the_transaction(pool_of_one):
    """The mechanism itself: SET LOCAL cannot outlive its transaction.

    This is what makes the isolation a property of Postgres rather than of this code remembering
    to clean up.
    """
    with pool_of_one.session(TENANT_A) as cur:
        cur.execute("SELECT current_setting(%s, true) AS scope", (TENANT_SETTING,))
        assert cur.fetchone()["scope"] == TENANT_A

    # Reach past the pool to the same physical connection and read the setting outside any
    # transaction the session() helper opened.
    conn = pool_of_one._pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_setting(%s, true)", (TENANT_SETTING,))
            leftover = cur.fetchone()[0]
        conn.rollback()
    finally:
        pool_of_one._pool.putconn(conn)
    assert leftover in (None, ""), (
        "app.tenant_id survived its transaction as %r; SET LOCAL is not being used" % leftover
    )


def test_an_exception_mid_query_still_clears_the_scope(pool_of_one):
    """A failed query must not leave a scope behind for the next tenant."""
    with pytest.raises(psycopg2.Error):
        with pool_of_one.session(TENANT_A) as cur:
            cur.execute("SELECT 1 FROM marts.no_such_table")

    rows = pool_of_one.fetch_all(TENANT_B, SELECT_ALL, ())
    assert {r["tenant_id"] for r in rows} == {TENANT_B}


def test_no_tenant_is_refused_not_silently_empty(pool_of_one):
    """Fail closed, and fail loudly.

    With app.tenant_id unset the policy matches no rows, so an empty tenant would return an empty
    result -- indistinguishable from "this tenant has no data". Refuse instead.
    """
    for bad in ("", None):
        with pytest.raises(TenantScopeError):
            pool_of_one.fetch_all(bad, SELECT_ALL, ())


def test_unscoped_connection_reads_nothing(pool_of_one):
    """Confirm the fail-closed property of the policy itself, independent of this code."""
    conn = pool_of_one._pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM marts.fct_t1")
            assert cur.fetchone()[0] == 0, (
                "an unscoped connection read rows; RLS is not failing closed"
            )
        conn.rollback()
    finally:
        pool_of_one._pool.putconn(conn)


def test_guard_trips_and_fails_closed_when_the_discipline_is_bypassed(pool_of_one):
    """Deliberately poison a connection with a session-level SET, then prove the guard catches it.

    This simulates the exact regression T-1 warns about: a future change using `SET` instead of
    `SET LOCAL`, or a code path that opens its own cursor. The guard must refuse the connection
    rather than serve a query on it.
    """
    conn = pool_of_one._pool.getconn()
    with conn.cursor() as cur:
        cur.execute("SET app.tenant_id = %s", (TENANT_A,))  # session scope, NOT local
    conn.commit()
    pool_of_one._pool.putconn(conn)

    before = pool_of_one.guard_trips
    with pytest.raises(PoolGuardTripped):
        pool_of_one.fetch_all(TENANT_B, SELECT_ALL, ())
    assert pool_of_one.guard_trips == before + 1

    # The poisoned connection was discarded, so the pool recovers rather than staying wedged.
    rows = pool_of_one.fetch_all(TENANT_B, SELECT_ALL, ())
    assert {r["tenant_id"] for r in rows} == {TENANT_B}
