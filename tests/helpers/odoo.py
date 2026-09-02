"""Drive Odoo through its own ORM, from the host, via ``odoo shell``.

Why the ORM and not SQL: the property under test in the live-sync case is that a **``unlink()``**
performed the way a user performs it reaches the mart as a tombstone. ADR 0001 chose logical
decoding precisely because ``write_date`` taps miss ``unlink()`` and ``ON DELETE CASCADE``. A test
that deleted the row with raw SQL would still exercise the WAL path, but it would stop being a
statement about Odoo and become a statement about Postgres.

Why ``odoo shell`` and not JSON-RPC: JSON-RPC needs a password for a real user. ``odoo shell``
attaches to the same database with the same ORM and needs no credential, so the suite has one fewer
secret to hold and works on a stack whose admin password was rotated.

``odoo shell`` **rolls back** on exit unless the code commits, so every mutation helper here calls
``env.cr.commit()`` explicitly. A create that is rolled back never reaches the WAL, and the
live-sync test would then fail for a reason that has nothing to do with the pipeline.
"""

from __future__ import annotations

import json
import subprocess

from .env import assert_project_scoped, env

ODOO_CONTAINER = "odoo19-bct-odoo"
MARKER = "@@QA_RESULT@@"


class OdooShellError(RuntimeError):
    pass


def shell(code: str, database=None, timeout=300):
    """Run ``code`` inside ``odoo shell``; return whatever it assigned to ``result``.

    The value is transported as one JSON line behind a marker, because the shell also emits a
    banner, the logger's output and a REPL prompt on the same stream.
    """
    assert_project_scoped(ODOO_CONTAINER)
    database = database or env("ODOO_DB_NAME", "bct")
    program = (
        "import json as _json\n"
        "result = None\n"
        f"{code}\n"
        f"print({MARKER!r} + _json.dumps(result, default=str))\n"
    )
    out = subprocess.run(
        ["docker", "exec", "-i", ODOO_CONTAINER,
         "odoo", "shell", "-d", database, "--no-http", "--log-level=warn"],
        input=program, capture_output=True, text=True, timeout=timeout,
    )
    for line in (out.stdout + "\n" + out.stderr).splitlines():
        if MARKER in line:
            return json.loads(line.split(MARKER, 1)[1])
    raise OdooShellError(
        f"odoo shell produced no result marker (rc={out.returncode})\n"
        f"--- stdout ---\n{out.stdout[-3000:]}\n--- stderr ---\n{out.stderr[-3000:]}"
    )


def create_partner(name: str, email: str, phone: str, database=None) -> int:
    return shell(
        "p = env['res.partner'].create({"
        f"'name': {name!r}, 'email': {email!r}, 'phone': {phone!r}"
        "})\n"
        "env.cr.commit()\n"
        "result = p.id",
        database=database,
    )


def write_partner(partner_id: int, values: dict, database=None) -> bool:
    return shell(
        f"p = env['res.partner'].browse({partner_id})\n"
        f"p.write({values!r})\n"
        "env.cr.commit()\n"
        "result = bool(p.exists())",
        database=database,
    )


def unlink_partner(partner_id: int, database=None) -> bool:
    """Delete through the ORM. Returns True when the row is genuinely gone from Odoo."""
    return shell(
        f"p = env['res.partner'].browse({partner_id})\n"
        "p.unlink()\n"
        "env.cr.commit()\n"
        f"result = not bool(env['res.partner'].browse({partner_id}).exists())",
        database=database,
    )


def authenticate(login: str, password: str, database=None, url=None):
    """Authenticate over Odoo's JSON-RPC endpoint. Returns the uid, or False.

    Deliberately the network path rather than `odoo shell`: a password is only meaningful if the
    thing that accepts logins accepts it. `odoo shell` bypasses authentication entirely, so a check
    made through it would pass on a stack whose credentials were never applied.
    """
    import json
    import urllib.request

    database = database or env("ODOO_DB_NAME", "bct")
    url = url or "http://127.0.0.1:%s/jsonrpc" % env("ODOO_HOST_HTTP_PORT", "38069")
    payload = {
        "jsonrpc": "2.0", "method": "call",
        "params": {"service": "common", "method": "authenticate",
                   "args": [database, login, password, {}]},
        "id": 1,
    }
    if not url.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise ValueError("refusing a non-loopback Odoo URL: %r" % url)
    request = urllib.request.Request(  # noqa: S310 - scheme checked immediately above
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        body = json.loads(response.read().decode("utf-8"))
    return body.get("result", False)
