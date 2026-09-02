#!/usr/bin/env bash
# Run the semantic API.
#
# Reserved port 38200 (contract 04 section 4), bound to 127.0.0.1 only.
#
# Connects as warehouse_rls, which holds SELECT and nothing else and is NOSUPERUSER NOBYPASSRLS, so
# row-level security genuinely applies to it. Never as `warehouse` (that is dbt's transform role,
# which has a policy allowing it to read unscoped) and never as `warehouse_admin` (a superuser,
# which bypasses RLS unconditionally). Contract 05 section A.2.
#
# It holds ONE database DSN, to the warehouse. It has no route to Odoo's OLTP Postgres at all,
# which is how anti-pattern 7.3 is prevented structurally rather than by policy.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
set -a; . "$ROOT/.env"; set +a

NAME="odoo19-bct-semantic-api"
DETACH=""
while [ $# -gt 0 ]; do
  case "$1" in
    --detach) DETACH="-d"; shift ;;
    *) break ;;
  esac
done

docker rm -f "$NAME" >/dev/null 2>&1 || true

DSN="host=${SEMANTIC_API_WAREHOUSE_HOST:-warehouse-db} port=${SEMANTIC_API_WAREHOUSE_PORT:-5432}"
DSN="$DSN dbname=${WAREHOUSE_DB} user=${WAREHOUSE_RLS_USER} password=${WAREHOUSE_RLS_PASSWORD}"

# The fallback is the gateway's CONTAINER name, not a compose service name. There is no
# compose service for the gateway in this repo -- scripts/analytics/gateway-run.sh starts it
# as `docker run --name odoo19-bct-login-gateway`, so that is the only name Docker's embedded
# DNS resolves on odoo19-bct_bct. Proven, not assumed: from a container on that network,
# `login-gateway` is NXDOMAIN while `odoo19-bct-login-gateway` serves the JWKS with both kids.
# A wrong value here rejects every valid token and looks like a client-side auth failure.

# MSYS_NO_PATHCONV is scoped to this one invocation, never exported (contract 04 section 11).
# shellcheck disable=SC2086
# Sized against the warehouse's connection budget, not against the current panel count:
# max_connections 40 - 3 reserved = 37, less dbt 5 (measured: DBT_THREADS+1), exporter ~3,
# CDC 3, ad-hoc psql ~4, margin. warehouse_ctl.py verify checks the total against the live limit.
# Exceeding it QUEUES for the acquire timeout and then sheds a documented 503 (contract 06 s2);
# it used to raise psycopg2 PoolError and surface as an undocumented 500.

exec env MSYS_NO_PATHCONV=1 docker run --rm $DETACH --name "$NAME" \
  --network odoo19-bct_bct \
  -p "${BIND_ADDRESS:-127.0.0.1}:${SEMANTIC_API_HOST_PORT:-38200}:8080" \
  --security-opt no-new-privileges \
  --cap-drop ALL \
  --read-only \
  -e SEMANTIC_API_WAREHOUSE_DSN="$DSN" \
  -e SEMANTIC_API_JWKS_URL="${SEMANTIC_API_JWKS_URL:-http://odoo19-bct-login-gateway:8080/.well-known/jwks.json}" \
  -e SEMANTIC_API_JWT_ISSUER="${SEMANTIC_API_JWT_ISSUER:-${LOGIN_GATEWAY_JWT_ISSUER}}" \
  -e SEMANTIC_API_JWT_AUDIENCE="${SEMANTIC_API_JWT_AUDIENCE:-${LOGIN_GATEWAY_JWT_AUDIENCE}}" \
  -e SEMANTIC_API_MAX_LIMIT="${SEMANTIC_API_MAX_LIMIT:-5000}" \
  -e SEMANTIC_API_POOL_MAX="${SEMANTIC_API_POOL_MAX:-16}" \
  -e SEMANTIC_API_POOL_ACQUIRE_TIMEOUT_MS="${SEMANTIC_API_POOL_ACQUIRE_TIMEOUT_MS:-2000}" \
  odoo19-bct-semantic-api:local "$@"
