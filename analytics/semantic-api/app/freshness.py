"""``meta.last_refreshed_at`` and ``meta.is_stale``, read from real pipeline metadata.

Contract 03 rule 3 and master prompt section 4: these come from ``warehouse.mart_freshness`` —
never from a clock on the client, and never from a clock here either. A service that computed
freshness from its own ``now()`` would report "fresh" whenever *it* had just restarted, which is
precisely when it knows least.

DWH's ``warehouse.mart_freshness`` view already joins ``pipeline_state`` to ``mart_sla`` and applies
the ADR's per-mart SLA, including the rule that **a mart with no pipeline_state row reports
``is_stale = true``** — never "fresh" by default. So this module reads that view rather than
recomputing the join, which would be a second implementation of a rule that has to agree.
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

FRESHNESS_SQL = """
SELECT last_refreshed_at, is_stale, sla_seconds, age_seconds
FROM warehouse.mart_freshness
WHERE mart_name = %s AND tenant_id = %s
"""

#: Returned when the view has no row for this mart/tenant. Fail stale, never fail fresh: an unknown
#: freshness is not a fresh one, and a dashboard that silently claims currency it cannot prove is
#: worse than one that admits it does not know.
UNKNOWN = {
    "last_refreshed_at": None,
    "is_stale": True,
    "sla_seconds": None,
    "age_seconds": None,
    "note": "No freshness record for this mart and tenant; reporting stale rather than assuming fresh.",
}


def read_freshness(warehouse, tenant_id: str, mart_name: str) -> dict:
    try:
        rows = warehouse.fetch_all(tenant_id, FRESHNESS_SQL, (mart_name, tenant_id))
    except Exception as exc:
        _logger.warning("freshness lookup failed for %s/%s: %s", tenant_id, mart_name, exc)
        return dict(UNKNOWN)
    if not rows:
        return dict(UNKNOWN)
    row = rows[0]
    stamp = row.get("last_refreshed_at")
    return {
        "last_refreshed_at": stamp.isoformat() if stamp is not None else None,
        "is_stale": bool(row.get("is_stale", True)),
        "sla_seconds": row.get("sla_seconds"),
        "age_seconds": (
            float(row["age_seconds"]) if row.get("age_seconds") is not None else None
        ),
    }
