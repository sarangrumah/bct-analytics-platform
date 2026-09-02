#!/usr/bin/env bash
# Entry point for the integration suite. `make test` calls this.
#
# Everything here runs from the HOST against the running project; the suite has no Python
# dependencies beyond pytest, because every database call goes through `docker exec ... psql` and
# every HTTP call through the standard library. That is deliberate: it exercises the containers the
# way an operator would rather than the way a driver would.
#
#   bash tests/run.sh                      the whole suite
#   bash tests/run.sh -k live_sync         one test
#   bash tests/run.sh -m "not slow"        skip the slow ones
#   bash tests/run.sh -s                   show the evidence blocks as they are produced
#   RUN_COLDSTART=1 bash tests/run.sh -m coldstart      DESTRUCTIVE, see test_11
#
# Skips are printed with their reasons (`-ra`). A component that does not exist yet produces a SKIP
# saying so, never a pass -- read the skip list, it is part of the result.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not on PATH; the suite talks to the odoo19-bct containers." >&2
  exit 2
fi

PY="${PYTHON:-python3}"
if ! "$PY" -c 'import pytest' >/dev/null 2>&1; then
  echo "pytest is not installed for $PY. Install it with: $PY -m pip install pytest" >&2
  exit 2
fi

# Stale __pycache__ on a Windows bind mount makes a container run yesterday's code against today's
# source. Cheap to clear, and it has already cost this project a debugging session.
find "$ROOT/addons" "$ROOT/tests" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "== odoo19-bct integration suite =="
docker ps --filter "name=odoo19-bct" --format '   {{.Names}}  {{.Status}}' || true
echo

exec "$PY" -m pytest tests -c tests/pytest.ini --rootdir "$ROOT" "$@"
