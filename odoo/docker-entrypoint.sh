#!/bin/bash
# ---------------------------------------------------------------------------
# Odoo entrypoint wrapper.
#
# Order matters:
#   1. render $ODOO_RC from the template (the upstream entrypoint and
#      wait-for-psql.py both read it, so it must exist first);
#   2. hand over to the upstream /entrypoint.sh, which keeps its wait-for-psql
#      behaviour and its `odoo` / `--` / bare-command argument handling.
#
# We deliberately do not reimplement the upstream entrypoint. Wrapping it means
# an image digest bump brings upstream fixes with it.
# ---------------------------------------------------------------------------
set -euo pipefail

python3 /usr/local/bin/render-config.py

# The upstream entrypoint derives db args from HOST/PORT/USER/PASSWORD only when
# the corresponding key is absent from $ODOO_RC. Ours sets all four, so it adds
# nothing and no password reaches argv.
exec /entrypoint.sh "$@"
