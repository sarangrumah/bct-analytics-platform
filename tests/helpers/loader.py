"""Start, stop and checksum the CDC loader from a test.

The loader is Backend's code and this suite does not modify it; it drives the documented entry
point ``scripts/analytics/cdc-run.sh`` exactly as an operator would, always under a container name
of this suite's own so that a test can never stop the long-running loader by accident.
"""

from __future__ import annotations

import subprocess

from . import db
from .env import assert_project_scoped, repo_root

MAIN = "odoo19-bct-cdc"


def run_loader(name, args, env_overrides=None, timeout=900, detach=False):
    """Invoke ``scripts/analytics/cdc-run.sh`` and return the CompletedProcess."""
    assert_project_scoped(name)
    import os

    environ = dict(os.environ)
    environ.setdefault("CDC_WAREHOUSE_HOST", "warehouse-db")
    environ.update(env_overrides or {})
    command = ["bash", "scripts/analytics/cdc-run.sh", "--name", name]
    if detach:
        command.append("--detach")
    command += ["--"] + list(args)
    return subprocess.run(
        command, capture_output=True, text=True, timeout=timeout,
        cwd=str(repo_root()), env=environ,
    )


#: Environment the loader needs, forwarded from `.env`. Mirrors `scripts/analytics/cdc-run.sh`.
_FORWARD = (
    "WAREHOUSE_READER_USER", "WAREHOUSE_READER_PASSWORD",
    "WAREHOUSE_DB", "WAREHOUSE_LOADER_USER", "WAREHOUSE_LOADER_PASSWORD",
    "WAREHOUSE_MASK_SALT_DEFAULT", "WAREHOUSE_MASK_SALT_BCT", "ODOO_DB_NAME",
)


def run_loader_direct(name, args, env_overrides=None, timeout=900, detach=False, capture=True):
    """`docker run` the loader image directly, for settings `cdc-run.sh` does not forward.

    `scripts/analytics/cdc-run.sh` forwards `CDC_TENANT_SLUG`, `CDC_SOURCE_TABLES` and friends but
    **not** `CDC_PUBLICATION` or `CDC_SLOT`, both of which `bct_cdc.config` reads. A test that needs
    to run a throwaway backfill against the existing publication under a slot name of its own
    therefore cannot go through the script. Same image, same code, same hardening flags -- only the
    invocation differs, and that difference is exactly the two variables above.
    """
    assert_project_scoped(name)
    from .env import dotenv

    values = dotenv()
    command = [
        "docker", "run", "--rm", "--name", name,
        "--network", "odoo19-bct_bct",
        "--security-opt", "no-new-privileges", "--cap-drop", "ALL", "--read-only",
    ]
    if detach:
        command.append("-d")
    settings = {
        "CDC_TENANT_DB": values.get("ODOO_DB_NAME", "bct"),
        "CDC_TENANT_SLUG": values.get("ODOO_DB_NAME", "bct"),
        "CDC_WAREHOUSE_HOST": "warehouse-db",
        "CDC_SOURCE_HOST": "postgres",
        "CDC_ODOO_URL": "http://odoo:8069",
        "CDC_VERIFY_DIGEST_SPEC": "0",
        "CDC_BATCH_SIZE": "2000",
    }
    for key in _FORWARD:
        if values.get(key) is not None:
            settings[key] = values[key]
    settings.update(env_overrides or {})
    for key, value in settings.items():
        command += ["-e", "%s=%s" % (key, value)]
    command += ["odoo19-bct-cdc:local"] + list(args)

    import os

    environ = dict(os.environ, MSYS_NO_PATHCONV="1")
    return subprocess.run(
        command, capture_output=capture, text=True, timeout=timeout, env=environ
    )


def popen_loader_direct(name, args, env_overrides=None):
    """Same as :func:`run_loader_direct` but non-blocking, so a test can kill it mid-run."""
    assert_project_scoped(name)
    import os

    from .env import dotenv

    values = dotenv()
    command = [
        "docker", "run", "--rm", "--name", name,
        "--network", "odoo19-bct_bct",
        "--security-opt", "no-new-privileges", "--cap-drop", "ALL", "--read-only",
    ]
    settings = {
        "CDC_TENANT_DB": values.get("ODOO_DB_NAME", "bct"),
        "CDC_TENANT_SLUG": values.get("ODOO_DB_NAME", "bct"),
        "CDC_WAREHOUSE_HOST": "warehouse-db",
        "CDC_SOURCE_HOST": "postgres",
        "CDC_ODOO_URL": "http://odoo:8069",
        "CDC_VERIFY_DIGEST_SPEC": "0",
        "CDC_BATCH_SIZE": "2000",
    }
    for key in _FORWARD:
        if values.get(key) is not None:
            settings[key] = values[key]
    settings.update(env_overrides or {})
    for key, value in settings.items():
        command += ["-e", "%s=%s" % (key, value)]
    command += ["odoo19-bct-cdc:local"] + list(args)
    return subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=dict(os.environ, MSYS_NO_PATHCONV="1"),
    )


def kill(name):
    assert_project_scoped(name)
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)


def stop_main(timeout=30):
    """Stop the long-running loader. Callers MUST restart it."""
    return subprocess.run(
        ["docker", "stop", "-t", "5", MAIN], capture_output=True, text=True, timeout=timeout
    )


def start_main(timeout=300):
    return run_loader(MAIN, [], detach=True, timeout=timeout)


LIVE_CHECKSUM = """
SELECT md5(string_agg(row_signature, '|' ORDER BY row_signature)) AS checksum, count(*)::text
FROM (
    SELECT md5(t.*::text) AS row_signature
    FROM (
        SELECT *, row_number() OVER (PARTITION BY _tenant_id, id
                                     ORDER BY _lsn DESC, _ingested_at DESC) AS _rn
        FROM raw.{table}
        WHERE _tenant_id = '{tenant}'
    ) t
    WHERE t._rn = 1 AND t._op <> 'D'
) signed;
"""


def live_checksum(target, table, tenant):
    """A content checksum of the *live projection*, ignoring `_ingested_at` volatility.

    `_ingested_at` is deliberately excluded from the signature: a genuinely idempotent re-load
    lands nothing, but a re-load that landed identical values at a later wall-clock time would
    otherwise look like a difference, and that is not what idempotency means here.
    """
    columns = [
        r[0] for r in db.query(
            target,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='raw' AND table_name='%s' "
            "AND column_name NOT IN ('_ingested_at','_row_id') ORDER BY ordinal_position;" % table,
        )
    ]
    projection = ", ".join('"%s"' % c for c in columns)
    sql = (
        "SELECT md5(string_agg(sig, '|' ORDER BY sig)), count(*)::text FROM ("
        "  SELECT md5(ROW(%s)::text) AS sig FROM ("
        "    SELECT *, row_number() OVER (PARTITION BY _tenant_id, id"
        "                                 ORDER BY _lsn DESC, _ingested_at DESC) AS _rn"
        "    FROM raw.%s WHERE _tenant_id = '%s'"
        "  ) t WHERE t._rn = 1 AND t._op <> 'D'"
        ") s;" % (projection, table, tenant)
    )
    return db.query(target, sql)[0]


def raw_row_count(target, table, tenant):
    return int(db.scalar(
        target, "SELECT count(*) FROM raw.%s WHERE _tenant_id = '%s';" % (table, tenant)
    ))
