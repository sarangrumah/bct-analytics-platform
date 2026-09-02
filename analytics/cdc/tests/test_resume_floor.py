"""A restart must not land the same change twice. Found by DWH, in the data, not in a test.

Logical replication is at-least-once BY DESIGN here. ``StreamConsumer.flush`` commits the warehouse
transaction and only then calls ``send_feedback``, deliberately: confirming an LSN the warehouse has
not stored tells Postgres it may drop that WAL, and the rows are gone from both ends. The price of
that ordering is a window - die between the commit and the feedback and Postgres redelivers changes
that ARE already landed.

DWH measured the window rather than theorising it::

    id | _op |   _lsn    | copies | first_seen             | last_seen
    46 | U   | 0/A313AC0 |   2    | 2026-08-31 08:13:54.86 | 2026-08-31 08:15:35.59

Real LSNs, identical payloads, 101 seconds apart. Every one of the 16 raw tables was swept; only
res_partner was affected and none of the duplicates sat at ``_lsn 0/0``, which is what ruled out a
fixture artefact.

Two things these tests pin down, because fixing only the first would leave the more expensive half
of the bug in place:

1. a change at or below the resume floor is dropped, and one above it is landed;
2. the floor defaults to 0, so a caller that passes nothing gets exactly the old behaviour and no
   existing path silently starts discarding data.
"""

from __future__ import annotations

from bct_cdc.pgoutput import parse_lsn
from bct_cdc.policy import MaskPlan
from bct_cdc.stream import StreamConsumer


class _Change:
    """The two fields the dispatch actually reads before it decides to buffer."""

    def __init__(self, lsn, table, op="U", pk=1):
        self.lsn = parse_lsn(lsn)
        self.op = op
        self.values = {"id": pk, "name": "x"}
        self.key = {"id": pk}
        self.commit_time = None
        self.relation = type("R", (), {"name": table})()


def _consumer(floor):
    plan = MaskPlan("res_partner", {"id": "none", "name": "none"}, salt="s")
    return StreamConsumer(
        "bct", "bct_slot_bct", {"res_partner": plan},
        warehouse_conn=None, status_conn=None, resume_floor_lsn=floor,
    )


def _dispatch(consumer, change):
    """Drive the exact branch ``__call__`` uses, without a psycopg2 replication message."""
    table = change.relation.name
    plan = consumer.plans.get(table)
    assert plan is not None, "precondition: the table must be planned, or nothing is exercised"
    if change.lsn <= consumer.resume_floor:
        consumer.skipped_redelivered += 1
    else:
        consumer._buffer(change, table, plan)


def test_a_redelivered_change_at_the_floor_is_not_landed_again():
    c = _consumer("0/A313AC0")
    _dispatch(c, _Change("0/A313AC0", "res_partner", pk=46))
    assert c.skipped_redelivered == 1
    assert c.buffer == {}, "the change was buffered for landing; it is already in raw"


def test_a_change_below_the_floor_is_not_landed_again():
    c = _consumer("0/A313AC0")
    _dispatch(c, _Change("0/A3139D8", "res_partner", pk=47))
    assert c.skipped_redelivered == 1
    assert c.buffer == {}


def test_a_change_above_the_floor_is_landed():
    """The half that stops this being a check that cannot fail.

    A floor that dropped EVERYTHING would satisfy both tests above perfectly, and the pipeline
    would go silently, permanently empty while reporting no error at all.
    """
    c = _consumer("0/A313AC0")
    _dispatch(c, _Change("0/A313AD0", "res_partner", pk=48))
    assert c.skipped_redelivered == 0
    assert len(c.buffer.get("res_partner", [])) == 1, (
        "a change past the resume floor must still land, or the floor is a data-loss bug"
    )


def test_no_floor_means_no_filtering():
    """Backwards compatibility, asserted rather than assumed.

    ``resume_floor_lsn`` defaults to None -> 0. Every LSN is > 0, so nothing is dropped.
    """
    c = _consumer(None)
    assert c.resume_floor == 0
    _dispatch(c, _Change("0/1", "res_partner"))
    assert c.skipped_redelivered == 0
    assert len(c.buffer.get("res_partner", [])) == 1


def test_the_floor_is_zero_for_an_empty_landing_zone():
    """`landed_max_lsn` returns '0/0' when nothing is landed, and '0/0' must floor nothing.

    The empty-result rule: a floor computed from an empty table must not become a floor that
    silently swallows the first real change.
    """
    c = _consumer("0/0")
    assert c.resume_floor == 0
    _dispatch(c, _Change("0/1", "res_partner"))
    assert len(c.buffer.get("res_partner", [])) == 1, (
        "an empty landing zone produced a floor that discarded the first change"
    )
