"""The "latest non-deleted version per key" projection, in one place.

Contract 05 makes this projection the definition of what the landing zone *means*: ``raw.*`` is
append-only, a delete is a tombstone, and "the current state" is the newest row per key with
``_op <> 'D'``. Every mart must apply it, so every reconciliation and idempotency assertion in this
suite must apply exactly the same one -- if the test used a different rule from the marts, the two
could disagree and neither would be wrong.

Ordering is ``(_lsn DESC, _ingested_at DESC)``, which is contract 05's ordering key
``(_tenant_id, <pk>, _lsn)`` read newest-first, with ``_ingested_at`` breaking ties for rows that
share an LSN (a single transaction touching one key more than once).
"""

from __future__ import annotations

LATEST = """
SELECT {columns}
FROM (
    SELECT *, row_number() OVER (PARTITION BY _tenant_id, id
                                 ORDER BY _lsn DESC, _ingested_at DESC) AS _rn
    FROM raw.{table}
    WHERE _tenant_id = '{tenant}'
) t
WHERE _rn = 1 AND _op <> 'D'
"""


def latest(table: str, tenant: str, columns: str = "*") -> str:
    """A subquery yielding the current state of ``raw.<table>`` for one tenant."""
    return LATEST.format(table=table, tenant=tenant, columns=columns)


def latest_count(table: str, tenant: str, where: str = "") -> str:
    inner = latest(table, tenant, "id")
    suffix = (" WHERE " + where) if where else ""
    return "SELECT count(*) FROM (%s) live%s;" % (inner, suffix)
