#!/usr/bin/env bash
# Run the CDC loader container against the running stack.
#
# Always `--network odoo19-bct_bct`, never a new network and never a published port. The loader
# needs no inbound traffic except the Prometheus scrape, which happens on the compose network.
#
# Hardening flags are here rather than only in a compose file so that an ad-hoc run has the same
# posture as a managed one: non-root (baked into the image), no-new-privileges, all capabilities
# dropped, read-only root filesystem. The loader writes nothing to disk -- its state lives in
# warehouse.pipeline_state and the landing zone itself.
#
# Usage: scripts/analytics/cdc-run.sh [--name NAME] [--detach] [-- ARGS...]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
set -a; . "$ROOT/.env"; set +a

NAME="odoo19-bct-cdc"
DETACH=""
while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --detach) DETACH="-d"; shift ;;
    --) shift; break ;;
    *) break ;;
  esac
done

docker rm -f "$NAME" >/dev/null 2>&1 || true

# MSYS_NO_PATHCONV is scoped to this one invocation, never exported (contract 04 section 11).
# Git Bash rewrites POSIX-looking arguments AND exported values before a native .exe sees them.
# shellcheck disable=SC2086
exec env MSYS_NO_PATHCONV=1 docker run --rm $DETACH --name "$NAME" \
  --network odoo19-bct_bct \
  --security-opt no-new-privileges \
  --cap-drop ALL \
  --read-only \
  -e WAREHOUSE_READER_USER -e WAREHOUSE_READER_PASSWORD \
  -e WAREHOUSE_DB -e WAREHOUSE_LOADER_USER -e WAREHOUSE_LOADER_PASSWORD \
  -e WAREHOUSE_MASK_SALT_DEFAULT -e WAREHOUSE_MASK_SALT_BCT \
  -e ODOO_DB_NAME \
  -e CDC_TENANT_DB="${CDC_TENANT_DB:-${ODOO_DB_NAME}}" \
  -e CDC_TENANT_SLUG="${CDC_TENANT_SLUG:-${ODOO_DB_NAME}}" \
  -e CDC_WAREHOUSE_HOST="${CDC_WAREHOUSE_HOST:-warehouse-db}" \
  -e CDC_SOURCE_HOST="${CDC_SOURCE_HOST:-postgres}" \
  -e CDC_ODOO_URL="${CDC_ODOO_URL:-http://odoo:8069}" \
  -e CDC_ODOO_LOGIN="${CDC_ODOO_LOGIN:-}" \
  -e CDC_ODOO_PASSWORD="${CDC_ODOO_PASSWORD:-}" \
  -e CDC_VERIFY_DIGEST_SPEC="${CDC_VERIFY_DIGEST_SPEC:-1}" \
  -e CDC_BATCH_SIZE="${CDC_BATCH_SIZE:-2000}" \
  -e CDC_SOURCE_TABLES="${CDC_SOURCE_TABLES:-}" \
  -e CDC_PUBLICATION="${CDC_PUBLICATION:-}" \
  -e CDC_SLOT="${CDC_SLOT:-}" \
  odoo19-bct-cdc:local "$@"
