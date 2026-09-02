"""Wire-format tests for the pgoutput decoder, built from hand-assembled messages.

Hand-assembled rather than captured, so a test failure points at one field rather than at "the
stream changed". The byte layouts come from the Postgres 16 "Logical Replication Message Formats"
documentation.
"""

from __future__ import annotations

import struct

import pytest

from bct_cdc.pgoutput import (
    UNCHANGED,
    PgOutputDecoder,
    TruncateSeen,
    format_lsn,
    parse_lsn,
)


def cstring(text):
    return text.encode("utf-8") + b"\x00"


def relation_message(relation_id=16384, name="sale_order_line", columns=(("id", 1), ("name", 0))):
    payload = b"R" + struct.pack("!I", relation_id) + cstring("public") + cstring(name) + b"d"
    payload += struct.pack("!h", len(columns))
    for column_name, flags in columns:
        payload += bytes([flags]) + cstring(column_name) + struct.pack("!I", 23) + struct.pack("!i", -1)
    return payload


def tuple_data(values):
    out = struct.pack("!h", len(values))
    for value in values:
        if value is None:
            out += b"n"
        elif value is UNCHANGED:
            out += b"u"
        else:
            encoded = value.encode("utf-8")
            out += b"t" + struct.pack("!i", len(encoded)) + encoded
    return out


def test_format_and_parse_lsn_round_trip():
    assert format_lsn(0x00000000_0AD54AD0) == "0/AD54AD0"
    assert parse_lsn("0/AD54AD0") == 0x0AD54AD0
    assert parse_lsn(format_lsn(0x1_2345_6789)) == 0x123456789


def test_insert_is_decoded_with_column_names():
    decoder = PgOutputDecoder()
    assert decoder.decode(relation_message(), 100) is None
    payload = b"I" + struct.pack("!I", 16384) + b"N" + tuple_data(["1232", "CDC-LIVE"])
    change = decoder.decode(payload, 200)
    assert change.op == "I"
    assert change.values == {"id": "1232", "name": "CDC-LIVE"}
    assert change.lsn == 200


def test_delete_carries_only_the_key_and_that_is_enough_for_a_tombstone():
    decoder = PgOutputDecoder()
    decoder.decode(relation_message(), 100)
    # REPLICA IDENTITY DEFAULT sends 'K' plus the key columns; the rest of the old tuple is absent
    # by design, and a tombstone only needs the identity.
    payload = b"D" + struct.pack("!I", 16384) + b"K" + tuple_data(["1232", None])
    change = decoder.decode(payload, 300)
    assert change.op == "D"
    assert change.key["id"] == "1232"
    assert change.values == {}


def test_update_with_a_key_block_is_decoded():
    decoder = PgOutputDecoder()
    decoder.decode(relation_message(), 100)
    payload = (
        b"U" + struct.pack("!I", 16384)
        + b"K" + tuple_data(["1232", None])
        + b"N" + tuple_data(["1232", "renamed"])
    )
    change = decoder.decode(payload, 400)
    assert change.op == "U"
    assert change.key["id"] == "1232"
    assert change.values["name"] == "renamed"


def test_update_without_a_key_block_falls_back_to_the_new_tuple():
    decoder = PgOutputDecoder()
    decoder.decode(relation_message(), 100)
    payload = b"U" + struct.pack("!I", 16384) + b"N" + tuple_data(["1232", "renamed"])
    change = decoder.decode(payload, 400)
    assert change.key == {"id": "1232"}


def test_unchanged_toast_is_a_sentinel_not_null():
    """The distinction that prevents silent data loss.

    Writing NULL for an unchanged TOASTed value blanks a large text column on every unrelated
    update -- a bug that only manifests on values big enough to be TOASTed, so it is found late.
    """
    decoder = PgOutputDecoder()
    decoder.decode(relation_message(), 100)
    payload = b"U" + struct.pack("!I", 16384) + b"N" + tuple_data(["1232", UNCHANGED])
    change = decoder.decode(payload, 500)
    assert change.values["name"] is UNCHANGED
    assert change.values["name"] is not None


def test_begin_and_commit_carry_no_change():
    decoder = PgOutputDecoder()
    begin = b"B" + struct.pack("!q", 1) + struct.pack("!q", 0) + struct.pack("!i", 42)
    assert decoder.decode(begin, 10) is None
    assert decoder.current_xid == 42
    commit = b"C" + bytes([0]) + struct.pack("!q", 1) + struct.pack("!q", 2) + struct.pack("!q", 0)
    assert decoder.decode(commit, 20) is None


def test_truncate_is_fatal_rather_than_ignored():
    """The landing zone has no representation for 'every row is gone'.

    Silently continuing past a TRUNCATE leaves the mart serving rows the source no longer has, with
    no error anywhere -- the exact class of silent drift the ADR chose logical decoding to avoid.
    """
    decoder = PgOutputDecoder()
    decoder.decode(relation_message(), 100)
    payload = b"T" + struct.pack("!i", 1) + bytes([0]) + struct.pack("!I", 16384)
    with pytest.raises(TruncateSeen) as exc:
        decoder.decode(payload, 600)
    assert "sale_order_line" in str(exc.value)


def test_change_before_its_relation_message_is_an_error():
    decoder = PgOutputDecoder()
    payload = b"I" + struct.pack("!I", 99999) + b"N" + tuple_data(["1"])
    with pytest.raises(ValueError):
        decoder.decode(payload, 700)


def test_unknown_message_tag_is_an_error_not_a_silent_skip():
    decoder = PgOutputDecoder()
    with pytest.raises(ValueError):
        decoder.decode(b"Z" + b"\x00" * 8, 800)


def test_ignorable_bookkeeping_messages_are_skipped():
    decoder = PgOutputDecoder()
    for tag in (b"Y", b"O", b"M"):
        assert decoder.decode(tag + b"\x00" * 8, 900) is None
