"""Repository root, ``.env`` loading, and the container names this suite is allowed to touch."""

from __future__ import annotations

import functools
import os
import pathlib
import subprocess

PROJECT = "odoo19-bct"

#: Every container this suite may address. A name not on this list is a bug, not a configuration
#: option: `odoo19-platform-*`, `odoo19-analytics-*` and `smart-warga-postgres-1` are other
#: people's live stacks on the same host and their data is not recoverable from here.
ALLOWED_CONTAINERS = frozenset(
    {
        f"{PROJECT}-odoo",
        f"{PROJECT}-postgres",
        f"{PROJECT}-redis",
        f"{PROJECT}-warehouse-db",
        f"{PROJECT}-cdc",
        f"{PROJECT}-cdc-fixture-db",
        # Short-lived loader runs this suite starts itself, always under a name of its own so a
        # test can never kill the long-running loader by accident.
        f"{PROJECT}-cdc-qa-reload",
        f"{PROJECT}-cdc-qa-resume",
        f"{PROJECT}-login-gateway",
        f"{PROJECT}-semantic-api",
        f"{PROJECT}-insight-portal",
        f"{PROJECT}-warehouse-exporter",
        f"{PROJECT}-postgres-exporter",
        f"{PROJECT}-prometheus",
        f"{PROJECT}-alertmanager",
        f"{PROJECT}-grafana",
        f"{PROJECT}-dbt",
    }
)


def repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def assert_project_scoped(container: str) -> str:
    """Fail loudly rather than run a command against another project's container."""
    if container not in ALLOWED_CONTAINERS:
        raise AssertionError(
            f"refusing to touch container {container!r}: it is not in project {PROJECT}. "
            "This host runs other live stacks whose data is not recoverable from this repo."
        )
    return container


@functools.lru_cache(maxsize=1)
def dotenv() -> dict:
    """Parse ``.env`` at the repository root.

    Not ``os.environ``: the values under test are the ones the containers were started with, and a
    shell that happened to export something else would make the suite lie about what it proved.
    """
    values = {}
    path = repo_root() / ".env"
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def env(name: str, default=None):
    """``.env`` first, then the process environment, then the default."""
    return dotenv().get(name, os.environ.get(name, default))


def container_env(container: str) -> dict:
    """Read a *running* container's environment.

    Used to discover where the CDC loader is actually writing rather than where a compose file says
    it should. Those have already differed once in this build.
    """
    assert_project_scoped(container)
    out = subprocess.run(
        ["docker", "inspect", container, "--format", "{{range .Config.Env}}{{println .}}{{end}}"],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        return {}
    result = {}
    for line in out.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            result[key] = value
    return result


def container_running(container: str) -> bool:
    assert_project_scoped(container)
    out = subprocess.run(
        ["docker", "ps", "--filter", f"name=^{container}$", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
    )
    return out.stdout.strip() == container
