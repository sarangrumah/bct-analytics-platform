"""A decoder for Postgres' native ``pgoutput`` logical replication protocol.

ADR 0001 mandates ``pgoutput``: not a ``write_date`` tap (it cannot see ``unlink()``, cascade
deletes, or direct SQL) and not Debezium (which drags in Kafka, anti-pattern 7.6). ``pgoutput`` is
in the server already, so the only cost is this file: the wire format has to be parsed by hand.

Protocol version 1 is used deliberately. Versions 2+ add in-progress transaction streaming, which
would let a half-applied transaction reach the landing zone; at our volumes the memory saving is
worth nothing and the correctness cost is real.

Reference: Postgres 16 "Logical Replication Message Formats". Every message begins with a one-byte
type tag; integers are big-endian.
"""

from __future__ import annotations

import datetime as dt
import struct
from dataclasses import dataclass, field

#: Postgres timestamps in the replication protocol count microseconds from 2000-01-01 UTC.
_PG_EPOCH = dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)


def pg_timestamp(micros: int) -> dt.datetime:
    return _PG_EPOCH + dt.timedelta(microseconds=micros)


def format_lsn(lsn: int) -> str:
    """Render a 64-bit LSN as Postgres' ``XXXXXXXX/XXXXXXXX`` text form."""
    return "%X/%X" % (lsn >> 32, lsn & 0xFFFFFFFF)


def parse_lsn(text: str) -> int:
    high, _, low = text.partition("/")
    return (int(high, 16) << 32) + int(low, 16)


@dataclass
class Column:
    name: str
    type_oid: int
    is_key: bool


@dataclass
class Relation:
    """An ``R`` message: the schema of one replicated table, sent before its first change."""

    relation_id: int
    namespace: str
    name: str
    replica_identity: str
    columns: list = field(default_factory=list)

    @property
    def key_columns(self) -> list:
        return [c.name for c in self.columns if c.is_key]


#: Sentinel for an unchanged TOASTed value (``u`` in the tuple format). It is *not* NULL: writing
#: NULL for it would silently blank a large text column on every unrelated update, which is a data
#: loss bug that only shows up on columns big enough to be TOASTed.
class _Unchanged:
    __slots__ = ()

    def __repr__(self) -> str:
        return "UNCHANGED"


UNCHANGED = _Unchanged()


@dataclass
class Change:
    """One decoded ``INSERT``, ``UPDATE`` or ``DELETE``."""

    op: str  # 'I' | 'U' | 'D'
    relation: Relation
    #: Column name -> value. ``None`` means SQL NULL; ``UNCHANGED`` marks an unchanged TOAST datum.
    values: dict
    #: For ``U``/``D`` under REPLICA IDENTITY DEFAULT this holds the old primary key.
    key: dict
    lsn: int
    commit_time: object = None
    xid: object = None


class TruncateSeen(RuntimeError):
    """A ``TRUNCATE`` was decoded.

    Deliberately fatal. The landing zone is append-only and has no representation for "every row is
    gone", so silently continuing would leave the mart serving rows the source no longer has.
    """

    def __init__(self, tables: list) -> None:
        super().__init__(
            "TRUNCATE decoded on "
            + ", ".join(tables or ["<unknown>"])
            + ". The landing zone cannot represent a truncate; a re-snapshot is required."
        )
        self.tables = tables


class _Reader:
    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes) -> None:
        self.buf = buf
        self.pos = 0

    def int8(self) -> int:
        v = self.buf[self.pos]
        self.pos += 1
        return v

    def char(self) -> str:
        return chr(self.int8())

    def int16(self) -> int:
        v = struct.unpack_from("!h", self.buf, self.pos)[0]
        self.pos += 2
        return v

    def int32(self) -> int:
        v = struct.unpack_from("!i", self.buf, self.pos)[0]
        self.pos += 4
        return v

    def uint32(self) -> int:
        v = struct.unpack_from("!I", self.buf, self.pos)[0]
        self.pos += 4
        return v

    def int64(self) -> int:
        v = struct.unpack_from("!q", self.buf, self.pos)[0]
        self.pos += 8
        return v

    def string(self) -> str:
        end = self.buf.index(b"\x00", self.pos)
        v = self.buf[self.pos : end].decode("utf-8")
        self.pos = end + 1
        return v

    def raw(self, n: int) -> bytes:
        v = self.buf[self.pos : self.pos + n]
        self.pos += n
        return v


class PgOutputDecoder:
    """Stateful decoder. ``Relation`` messages are remembered and applied to later changes."""

    def __init__(self) -> None:
        self.relations = {}
        self.current_xid = None
        self.current_commit_time = None

    # -- tuple data ------------------------------------------------------------------

    def _tuple(self, r: _Reader, relation: Relation) -> dict:
        ncols = r.int16()
        out = {}
        for index in range(ncols):
            kind = r.char()
            name = relation.columns[index].name if index < len(relation.columns) else "col%d" % index
            if kind == "n":
                out[name] = None
            elif kind == "u":
                out[name] = UNCHANGED
            elif kind in ("t", "b"):
                length = r.int32()
                data = r.raw(length)
                # proto v1 always uses text format; decode 'b' defensively rather than crashing.
                out[name] = data.decode("utf-8", errors="replace")
            else:  # pragma: no cover - an unknown tuple kind means the protocol changed
                raise ValueError("Unknown pgoutput tuple data kind %r" % kind)
        return out

    # -- message dispatch ------------------------------------------------------------

    def decode(self, payload: bytes, wal_start: int):
        """Decode one replication message. Returns a :class:`Change`, or ``None`` for bookkeeping.

        ``wal_start`` is the stream's ``data_start`` for this message and becomes ``_lsn`` in the
        landing zone: it is unique per change and monotonic, which is what makes
        ``(_tenant_id, id, _lsn)`` a safe idempotency key.
        """
        if not payload:
            return None
        r = _Reader(payload)
        tag = r.char()

        if tag == "B":  # Begin
            r.int64()  # final LSN
            self.current_commit_time = pg_timestamp(r.int64())
            self.current_xid = r.int32()
            return None

        if tag == "C":  # Commit
            r.int8()  # flags
            r.int64()  # commit LSN
            r.int64()  # end LSN
            self.current_commit_time = pg_timestamp(r.int64())
            self.current_xid = None
            return None

        if tag == "R":  # Relation
            relation_id = r.uint32()
            namespace = r.string() or "pg_catalog"
            name = r.string()
            replica_identity = r.char()
            ncols = r.int16()
            columns = []
            for _ in range(ncols):
                flags = r.int8()
                columns.append(Column(name=r.string(), type_oid=r.uint32(), is_key=bool(flags & 1)))
                r.int32()  # atttypmod
            self.relations[relation_id] = Relation(
                relation_id=relation_id,
                namespace=namespace,
                name=name,
                replica_identity=replica_identity,
                columns=columns,
            )
            return None

        if tag in ("Y", "O", "M"):  # Type, Origin, logical decoding Message -- not used here
            return None

        if tag == "T":  # Truncate
            nrel = r.int32()
            r.int8()  # flags
            ids = [r.uint32() for _ in range(nrel)]
            names = [self.relations[i].name for i in ids if i in self.relations]
            raise TruncateSeen(names)

        if tag in ("I", "U", "D"):
            relation = self.relations.get(r.uint32())
            if relation is None:  # pragma: no cover - the server always sends R first
                raise ValueError("pgoutput change arrived before its Relation message")
            key = {}
            values = {}
            if tag == "I":
                r.char()  # always 'N'
                values = self._tuple(r, relation)
            elif tag == "U":
                sub = r.char()
                if sub in ("K", "O"):
                    key = self._tuple(r, relation)
                    r.char()  # the following 'N'
                values = self._tuple(r, relation)
            else:  # 'D'
                r.char()  # 'K' under REPLICA IDENTITY DEFAULT, 'O' under FULL
                key = self._tuple(r, relation)
                values = {}
            if not key:
                key = {c: values.get(c) for c in relation.key_columns}
            return Change(
                op=tag,
                relation=relation,
                values=values,
                key=key,
                lsn=wal_start,
                commit_time=self.current_commit_time,
                xid=self.current_xid,
            )

        raise ValueError("Unknown pgoutput message tag %r" % tag)
