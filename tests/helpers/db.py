"""Postgres access, through ``docker exec ... psql``.

Two properties matter more than convenience here.

**Authentication is real.** Every connection is made over TCP to ``127.0.0.1`` *inside* the
container, never over the unix socket, so ``pg_hba``'s ``trust``/``peer`` entries cannot quietly
promote the caller. A test that thinks it is ``warehouse_rls`` and is actually the bootstrap
superuser proves nothing at all.

**Identity is a returned value, not an assumption.** :func:`role_identity` returns real booleans
parsed from psql's ``t``/``f``, and :func:`assert_rls_subject` is what every isolation test calls
first. Contract 05 §A spells out why: RLS is *never* evaluated for a ``SUPERUSER`` or a
``BYPASSRLS`` role, so an isolation test pointed at one passes for ever while testing nothing. The
same section records the second half of the trap -- rendering those booleans through ``||`` yields
``true``/``false``, not ``t``/``f``, so a string comparison against ``'f'`` silently never matches.
This module sidesteps both by parsing the column and returning ``bool``.
"""

from __future__ import annotations

import dataclasses
import subprocess

from .env import assert_project_scoped, env

SEP = "\x1f"   # ASCII unit separator: cannot occur in the values we select
#: Sentinel psql prints for SQL NULL, so a NULL is distinguishable from an empty string -- a
#: distinction this suite depends on, because contract 05 makes `''` and NULL mean different things
#: in a masked column (`""` in -> NULL out, never a shared digest).
#:
#: Deliberately neither "\x00" nor a control character. "\x00" cannot be passed at all: Windows
#: CreateProcess rejects any argument containing a NUL byte, so it fails inside subprocess before
#: docker is reached. "\x1e" *is* accepted and then silently arrives as an empty string, which is
#: the exact confusion this constant exists to remove. A long printable token survives both hops.
NULL = "__PG_NULL_a3f9__"


class PsqlError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class Target:
    """A (container, role, database) triple this suite can query."""

    container: str
    user: str
    password: str
    database: str
    label: str = ""

    def __post_init__(self):
        assert_project_scoped(self.container)


def _run(target: Target, args, sql_text=None, timeout=120):
    cmd = [
        "docker", "exec",
        "-e", f"PGPASSWORD={target.password}",
        target.container,
        "psql",
        "-h", "127.0.0.1",
        "-U", target.user,
        "-d", target.database,
        "-v", "ON_ERROR_STOP=1",
        *args,
    ]
    if sql_text is not None:
        cmd += ["-c", sql_text]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def query(target: Target, sql_text: str, timeout=120) -> list:
    """Run one statement and return a list of tuples of ``str | None``."""
    out = _run(target, ["-At", "-F", SEP, "-P", "null=" + NULL], sql_text, timeout=timeout)
    if out.returncode != 0:
        raise PsqlError(f"{target.label or target.user}: {out.stderr.strip()}")
    rows = []
    for line in out.stdout.splitlines():
        if line == "":
            continue
        rows.append(tuple(None if c == NULL else c for c in line.split(SEP)))
    return rows


def scalar(target: Target, sql_text: str, timeout=120):
    rows = query(target, sql_text, timeout=timeout)
    return rows[0][0] if rows else None


def execute(target: Target, sql_text: str, timeout=120):
    """Run a statement for effect. Returns ``(returncode, stdout, stderr)`` -- errors are data.

    Permission tests need the *denial message*, so a failure is never raised here.
    """
    out = _run(target, [], sql_text, timeout=timeout)
    return out.returncode, out.stdout.strip(), out.stderr.strip()


def grid(target: Target, sql_text: str, timeout=120) -> str:
    """The human-readable psql result grid, for pasting into evidence verbatim."""
    out = _run(target, [], sql_text, timeout=timeout)
    if out.returncode != 0:
        raise PsqlError(f"{target.label or target.user}: {out.stderr.strip()}")
    return out.stdout.rstrip()


def scoped_query(target: Target, tenant: str, sql_text: str, arity: int) -> list:
    """Run ``sql_text`` with ``app.tenant_id`` set to ``tenant`` for that statement only.

    ``SET LOCAL`` inside an explicit transaction, which is contract 05's T-1: RLS reads a *session*
    variable, so a plain ``SET`` on a pooled connection stays set after checkin and the next
    tenant's query silently inherits it. ``SET LOCAL`` cannot outlive the transaction.

    ``arity`` filters the output down to the rows the SELECT produced. psql emits ``BEGIN``,
    ``SET`` and ``COMMIT`` as their own lines on the same stream, and they parse as one-column rows
    -- taking "the last row" without filtering picks up ``COMMIT``.
    """
    statement = "BEGIN; SET LOCAL app.tenant_id = %s; %s COMMIT;" % (quote_literal(tenant), sql_text)
    return [row for row in query(target, statement) if len(row) == arity]


def quote_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


IDENTITY_SQL = "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;"


@dataclasses.dataclass(frozen=True)
class Identity:
    user: str
    superuser: bool
    bypassrls: bool
    grid: str

    @property
    def rls_applies(self) -> bool:
        return not self.superuser and not self.bypassrls


def role_identity(target: Target) -> Identity:
    """Ask the server who we are. ``t``/``f`` are parsed into real booleans on purpose."""
    rows = query(target, IDENTITY_SQL)
    if not rows:
        raise PsqlError(f"no pg_roles row for current_user on {target.container}")
    user, super_flag, bypass_flag = rows[0]
    return Identity(
        user=user,
        superuser=(super_flag == "t"),
        bypassrls=(bypass_flag == "t"),
        grid=grid(target, IDENTITY_SQL),
    )


def assert_rls_subject(target: Target) -> Identity:
    """The precondition every isolation test must state before it asserts anything else."""
    identity = role_identity(target)
    assert identity.rls_applies, (
        "This connection BYPASSES row-level security, so any isolation assertion below it is "
        f"vacuous.\n{identity.grid}\n"
        f"superuser={identity.superuser} bypassrls={identity.bypassrls} "
        "(both must be False for RLS to be evaluated at all)"
    )
    return identity


# --------------------------------------------------------------------------------------------
# The targets this suite knows about
# --------------------------------------------------------------------------------------------

WAREHOUSE_CONTAINER = "odoo19-bct-warehouse-db"
FIXTURE_CONTAINER = "odoo19-bct-cdc-fixture-db"
OLTP_CONTAINER = "odoo19-bct-postgres"


def warehouse_admin(container=WAREHOUSE_CONTAINER) -> Target:
    return Target(
        container,
        env("WAREHOUSE_ADMIN_USER", "warehouse_admin"),
        env("WAREHOUSE_ADMIN_PASSWORD", ""),
        env("WAREHOUSE_DB", "warehouse"),
        "warehouse_admin (SUPERUSER -- never used for an isolation assertion)",
    )


def warehouse_dbt(container=WAREHOUSE_CONTAINER) -> Target:
    return Target(
        container,
        env("WAREHOUSE_DB_USER", "warehouse"),
        env("WAREHOUSE_DB_PASSWORD", ""),
        env("WAREHOUSE_DB", "warehouse"),
        "warehouse (dbt)",
    )


def warehouse_loader(container=WAREHOUSE_CONTAINER) -> Target:
    return Target(
        container,
        env("WAREHOUSE_LOADER_USER", "warehouse_loader"),
        env("WAREHOUSE_LOADER_PASSWORD", ""),
        env("WAREHOUSE_DB", "warehouse"),
        "warehouse_loader (CDC)",
    )


def warehouse_rls(container=WAREHOUSE_CONTAINER) -> Target:
    return Target(
        container,
        env("WAREHOUSE_RLS_USER", "warehouse_rls"),
        env("WAREHOUSE_RLS_PASSWORD", ""),
        env("WAREHOUSE_DB", "warehouse"),
        "warehouse_rls (semantic-api)",
    )


def oltp_odoo(database=None) -> Target:
    return Target(
        OLTP_CONTAINER,
        env("POSTGRES_USER", "odoo"),
        env("POSTGRES_PASSWORD", ""),
        database or env("ODOO_DB_NAME", "bct"),
        "odoo (OLTP owner)",
    )


def oltp_reader(database=None) -> Target:
    return Target(
        OLTP_CONTAINER,
        env("WAREHOUSE_READER_USER", "warehouse_reader"),
        env("WAREHOUSE_READER_PASSWORD", ""),
        database or env("ODOO_DB_NAME", "bct"),
        "warehouse_reader (OLTP, read-only by construction)",
    )
