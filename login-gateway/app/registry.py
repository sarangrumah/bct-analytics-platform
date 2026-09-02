"""The control-plane lookup behind the diagram's "Active?" decision.

WHY THIS LIVES IN THE GATEWAY. The decision has to happen where identity is
already established and before a session exists, and that is exactly here. Any
later — in the portal, say — and a suspended client already holds a valid
token; any earlier and there is no tenant to ask about.

WHY IT IS A DATABASE READ AND NOT AN API CALL. `tenant_registry.is_active()`
is a function in the control-plane database, and it is the ONLY implementation
of that rule in the platform. The orchestrator, the super-admin console and
this gateway all consult the same function rather than each carrying their own
copy of "state = active AND valid_until has not passed". Three copies of that
rule eventually disagree, and the way it shows up is a suspended client keeping
a working dashboard.

THE IDENTITY IS DELIBERATELY TINY. The DSN here is for a role that holds SELECT
on `tenant_registry` and EXECUTE on two functions, and nothing else. It cannot
read a single row of any tenant's data — not because this code is careful, but
because the role has no grant that would let it. That is the same posture
`warehouse_rls` has in the semantic API.

FAIL-CLOSED, AND WHAT THAT COSTS. If the registry is configured but unreachable,
every lookup answers "not active". A client then lands on the subscription page
instead of the dashboard, which is wrong but safe and self-explaining. The
alternative — treating an unreachable control plane as "everyone is paid up" —
turns a database outage into an entitlement bypass. The cost is real and is
stated here so nobody discovers it during an incident: a control-plane outage
is a platform-wide dashboard outage.
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger("login_gateway.registry")

#: What a tenant gets when no control plane is configured at all. Distinct from
#: the fail-closed case on purpose: "not configured" is a deployment that has
#: not adopted entitlements yet, and refusing every login there would break a
#: working stack on upgrade. It is loud rather than silent — see Registry.
_ALL_PRODUCTS = ("insight", "odoo", "agent")


class Entitlement:
    """The answer to one question: may this tenant open a session, and to what."""

    __slots__ = ("active", "products", "source")

    def __init__(self, active: bool, products, source: str) -> None:
        self.active = bool(active)
        self.products = tuple(products)
        self.source = source

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "Entitlement(active=%r, products=%r, source=%r)" % (
            self.active, self.products, self.source)


class Registry:
    """Reads tenant_registry, with a short positive cache.

    The cache exists because this is consulted on every login AND every refresh,
    and a refresh happens far more often than a subscription changes. Its TTL is
    the window in which a just-suspended tenant can still refresh a session; it
    is short and configurable for exactly that reason. Nothing is cached on
    failure, so an outage does not pin every tenant to "inactive" for longer
    than it lasts.
    """

    def __init__(self, dsn: str | None, cache_ttl: int = 30) -> None:
        self._dsn = dsn or ""
        self._ttl = max(0, int(cache_ttl))
        self._cache: dict[str, tuple[float, Entitlement]] = {}
        self._lock = threading.Lock()
        self._pg = None

        if not self._dsn:
            # WARNING, not INFO. A platform running without entitlement
            # enforcement should say so on every boot; discovering it from a
            # billing report instead is how unpaid tenants stay served.
            logger.warning(
                "LOGIN_GATEWAY_REGISTRY_DSN is not set: subscription enforcement is OFF. "
                "Every authenticated tenant will be issued a token claiming all products.")
            return

        try:
            import psycopg2  # noqa: PLC0415 - optional at import time by design
        except ImportError:  # pragma: no cover - a build without the driver
            logger.error(
                "psycopg2 is unavailable but LOGIN_GATEWAY_REGISTRY_DSN is set; "
                "entitlement lookups will fail closed.")
            return
        self._pg = psycopg2

    @property
    def configured(self) -> bool:
        return bool(self._dsn)

    def lookup(self, slug: str) -> Entitlement:
        if not self._dsn:
            return Entitlement(True, _ALL_PRODUCTS, "unconfigured")

        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(slug)
            if hit and hit[0] > now:
                return hit[1]

        result = self._query(slug)

        # Only a successful read is cached. A failure must be retried on the
        # next request rather than held for the TTL, or a two-second blip
        # becomes a thirty-second outage.
        if result.source == "registry":
            with self._lock:
                self._cache[slug] = (now + self._ttl, result)
        return result

    def _query(self, slug: str) -> Entitlement:
        if self._pg is None:
            return Entitlement(False, (), "driver-missing")
        conn = None
        try:
            conn = self._pg.connect(self._dsn, connect_timeout=3)
            conn.set_session(readonly=True, autocommit=True)
            with conn.cursor() as cur:
                # One round trip, and both answers come from the database's own
                # functions rather than from a SELECT this file assembles. If
                # the rule changes, it changes in one place.
                cur.execute(
                    "SELECT tenant_registry.is_active(%s), "
                    "       tenant_registry.entitlements(%s)",
                    (slug, slug))
                row = cur.fetchone()
            if not row:
                return Entitlement(False, (), "registry")
            return Entitlement(bool(row[0]), tuple(row[1] or ()), "registry")
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            logger.error("tenant registry lookup failed for %r, failing closed: %s", slug, exc)
            return Entitlement(False, (), "error")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception as exc:  # noqa: BLE001 - never raised from a finally
                    # Logged rather than swallowed: a close() that keeps failing
                    # is how a pool leaks, and a bare `pass` here would hide it
                    # for as long as the process lives.
                    logger.debug("closing the registry connection failed: %s", exc)

    def invalidate(self, slug: str | None = None) -> None:
        """Drop cached answers. Used by tests; also the hook a future
        orchestrator webhook would call when a subscription changes."""
        with self._lock:
            if slug is None:
                self._cache.clear()
            else:
                self._cache.pop(slug, None)
