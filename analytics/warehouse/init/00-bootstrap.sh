#!/bin/sh
# ===========================================================================
# 00-bootstrap.sh — applied by the Postgres entrypoint on FIRST BOOT only.
#
# The entrypoint executes files directly in /docker-entrypoint-initdb.d and
# ignores subdirectories, which is exactly what lets this script apply
# init/sql/*.sql itself with `psql -v`. Passing the role passwords as psql
# variables is the point: no credential is ever written into a tracked SQL
# file, and none appears in the process list of a `docker inspect`.
#
# The SAME sql/ directory is re-applied on every `make up-analytics` by
# analytics/warehouse/bin/warehouse-apply.sh, which is why every statement in
# it is idempotent. Two paths, one source of DDL — a schema change is picked
# up by an existing volume rather than needing it destroyed.
# ===========================================================================
set -eu

: "${WAREHOUSE_DB_USER:?WAREHOUSE_DB_USER is required}"
: "${WAREHOUSE_DB_PASSWORD:?WAREHOUSE_DB_PASSWORD is required}"
: "${WAREHOUSE_LOADER_USER:?WAREHOUSE_LOADER_USER is required}"
: "${WAREHOUSE_LOADER_PASSWORD:?WAREHOUSE_LOADER_PASSWORD is required}"
: "${WAREHOUSE_RLS_USER:?WAREHOUSE_RLS_USER is required}"
: "${WAREHOUSE_RLS_PASSWORD:?WAREHOUSE_RLS_PASSWORD is required}"

SQL_DIR=/docker-entrypoint-initdb.d/sql

echo "warehouse bootstrap: applying ${SQL_DIR}/*.sql to database ${POSTGRES_DB} as ${POSTGRES_USER}"

for f in "$SQL_DIR"/*.sql; do
  echo "warehouse bootstrap: -> $(basename "$f")"
  # ON_ERROR_STOP=1 so a failed assertion (for example the PDP known-answer
  # vectors in 60-functions.sql) aborts the whole initialisation instead of
  # leaving a half-built warehouse that looks healthy.
  psql -v ON_ERROR_STOP=1 \
       --username "$POSTGRES_USER" \
       --dbname "$POSTGRES_DB" \
       -v wh_user="$WAREHOUSE_DB_USER" \
       -v wh_password="$WAREHOUSE_DB_PASSWORD" \
       -v loader_user="$WAREHOUSE_LOADER_USER" \
       -v loader_password="$WAREHOUSE_LOADER_PASSWORD" \
       -v rls_user="$WAREHOUSE_RLS_USER" \
       -v rls_password="$WAREHOUSE_RLS_PASSWORD" \
       -f "$f"
done

echo "warehouse bootstrap: done"
