"""Fixtures shared by the odoo19-bct integration suite.

Read `tests/README.md` first. The two rules that shape everything in this file:

1. **Nothing outside project ``odoo19-bct`` is ever addressed.** Enforced in
   ``helpers.env.assert_project_scoped``, not by convention.
2. **A component that does not exist yet produces a SKIP with a reason, never a pass.** The brief is
   explicit: a test that cannot run is reported as not-run. ``pytest -ra`` prints every skip reason,
   so "not covered" is visible in the same output as "passed" rather than hidden behind a green bar.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import db, env, web  # noqa: E402

CDC_CONTAINER = "odoo19-bct-cdc"


# ---------------------------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------------------------


class Evidence:
    """Collects verbatim command output so a report can quote it rather than paraphrase it."""

    def __init__(self, name):
        self.name = name
        self.blocks = []

    def add(self, title, text):
        self.blocks.append((title, str(text).rstrip()))
        print(f"\n----- {self.name} :: {title} -----\n{str(text).rstrip()}", flush=True)

    def dump(self):
        return "\n\n".join(f"----- {t} -----\n{b}" for t, b in self.blocks)


@pytest.fixture
def evidence(request):
    ev = Evidence(request.node.name)
    yield ev


# ---------------------------------------------------------------------------------------------
# Stack preconditions
# ---------------------------------------------------------------------------------------------


def _require(container):
    if not env.container_running(container):
        pytest.skip(f"container {container} is not running (NOT RUN, not passed)")


@pytest.fixture(scope="session")
def odoo_up():
    _require("odoo19-bct-odoo")


@pytest.fixture(scope="session")
def oltp_up():
    _require("odoo19-bct-postgres")


@pytest.fixture(scope="session")
def warehouse_up():
    _require("odoo19-bct-warehouse-db")


@pytest.fixture(scope="session")
def cdc_target():
    """The warehouse the CDC loader is **actually** writing to, read off the running container.

    This is not paranoia. At the time this suite was written the compose file wired the loader to
    ``warehouse-db`` while the process that was actually running had been started by
    ``scripts/analytics/cdc-run.sh`` with ``CDC_WAREHOUSE_HOST=odoo19-bct-cdc-fixture-db`` -- a
    single-role superuser fixture database with no RLS at all. A live-sync test that assumed the
    compose wiring would have reported on an empty table; an isolation test that assumed it would
    have passed while proving nothing. So the target is discovered, and every test that uses it
    prints which database it used.
    """
    if env.container_running(CDC_CONTAINER):
        host = env.container_env(CDC_CONTAINER).get("CDC_WAREHOUSE_HOST", "warehouse-db")
    else:
        host = env.env("CDC_WAREHOUSE_HOST", "warehouse-db")
    # The value is a hostname on the compose network; map it to the container name.
    container = {
        "warehouse-db": db.WAREHOUSE_CONTAINER,
        "odoo19-bct-warehouse-db": db.WAREHOUSE_CONTAINER,
        "odoo19-bct-cdc-fixture-db": db.FIXTURE_CONTAINER,
        "cdc-fixture-db": db.FIXTURE_CONTAINER,
    }.get(host, db.WAREHOUSE_CONTAINER)
    return container


@pytest.fixture
def cdc_warehouse(cdc_target):
    """A Target on whichever warehouse the loader writes to, reading as the loader's own role.

    ``warehouse_loader`` rather than ``warehouse_admin`` on purpose: it holds exactly ``SELECT`` +
    ``INSERT`` on ``raw.*``, so reading the landing zone through it also demonstrates that the
    landing zone is readable by the identity that wrote it, and keeps the superuser out of the
    evidence entirely. Reading ``raw`` is not an isolation assertion -- ``raw.*`` carries no RLS --
    but a superuser in the evidence grid invites exactly the misreading contract 05 §A warns about.

    The one historical exception was the now-removed ``odoo19-bct-cdc-fixture-db``, whose only role
    was a superuser; it is handled so this fixture still works if such a target ever reappears.
    """
    _require(cdc_target)
    if cdc_target == db.FIXTURE_CONTAINER:
        return db.Target(
            cdc_target,
            env.env("WAREHOUSE_DB_USER", "warehouse"),
            env.env("WAREHOUSE_DB_PASSWORD", ""),
            env.env("WAREHOUSE_DB", "warehouse"),
            "warehouse (CDC dev fixture database -- SUPERUSER, no RLS)",
        )
    return db.warehouse_loader(cdc_target)


@pytest.fixture(scope="session")
def cdc_running():
    if not env.container_running(CDC_CONTAINER):
        pytest.skip(
            "the CDC loader is not running, so no live change can reach the warehouse. "
            "Start it with `bash scripts/analytics/cdc-run.sh --detach` and re-run. (NOT RUN)"
        )


@pytest.fixture(scope="session")
def gateway_up():
    _require("odoo19-bct-login-gateway")
    if not web.service_up(web.gateway_url("/healthz")):
        pytest.skip("login-gateway is not answering /healthz (NOT RUN)")


@pytest.fixture(scope="session")
def semantic_up():
    if not env.container_running("odoo19-bct-semantic-api"):
        pytest.skip(
            "semantic-api does not exist yet (Backend, phase 3). Test written, NOT RUN."
        )
    if not web.service_up(web.semantic_url("/healthz")):
        pytest.skip("semantic-api is not answering /healthz (NOT RUN)")


@pytest.fixture(scope="session")
def marts_exist(warehouse_up):
    """dbt marts. Absent until DWH gets `dbt build` green -- which it had not, when this was written."""
    rows = db.query(
        db.warehouse_admin(),
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'marts' ORDER BY 1;",
    )
    names = [r[0] for r in rows]
    if not names:
        pytest.skip(
            "schema `marts` is empty: no dbt model has been built. "
            "Run `make dbt-run` once DWH has it green. Test written, NOT RUN."
        )
    return names


# ---------------------------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------------------------


def wait_for(predicate, timeout, interval=1.0, description=""):
    """Poll ``predicate`` and return ``(value, elapsed_seconds)``; value is falsy on timeout."""
    started = time.monotonic()
    deadline = started + timeout
    value = None
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value, time.monotonic() - started
        time.sleep(interval)
    return value, time.monotonic() - started


def run(cmd, timeout=600, cwd=None):
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        cwd=cwd or str(env.repo_root()), shell=isinstance(cmd, str),
    )
