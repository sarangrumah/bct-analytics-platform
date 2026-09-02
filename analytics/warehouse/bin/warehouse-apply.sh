#!/usr/bin/env bash
# ===========================================================================
# warehouse-apply.sh — (re)apply the warehouse DDL to a RUNNING warehouse-db.
#
#   bash analytics/warehouse/bin/warehouse-apply.sh
#
# Applies exactly the same analytics/warehouse/init/sql/*.sql that the
# container entrypoint applies at first boot, in the same order, with the same
# psql variables. Every statement in those files is idempotent, so this is
# safe to run on every `make up-analytics` — and running it on every up is
# what stops the schema and the tracked DDL from drifting apart the moment
# somebody adds a table without destroying their volume.
#
# Scoped `-p odoo19-bct` throughout. This host also runs odoo19-platform-*,
# odoo19-analytics-* and smart-warga-postgres-1; nothing here can reach them.
# ===========================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"

PROJECT="${PROJECT:-odoo19-bct}"
DC=(docker compose -p "$PROJECT" --env-file .env -f compose/odoo.yml -f compose/odoo.dev.yml -f compose/insight.yml)

# shellcheck disable=SC1091
set -a; . ./.env; set +a

WH_ADMIN="${WAREHOUSE_ADMIN_USER:-warehouse_admin}"
WH_DB="${WAREHOUSE_DB:-warehouse}"

echo "==> waiting for warehouse-db to accept connections"
for _ in $(seq 1 60); do
  if "${DC[@]}" exec -T warehouse-db pg_isready -h 127.0.0.1 -U "$WH_ADMIN" -d "$WH_DB" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
"${DC[@]}" exec -T warehouse-db pg_isready -h 127.0.0.1 -U "$WH_ADMIN" -d "$WH_DB" >/dev/null

echo "==> applying analytics/warehouse/init/sql/*.sql"
for f in analytics/warehouse/init/sql/*.sql; do
  base="$(basename "$f")"
  echo "    -> $base"
  # The files are read from inside the container at the read-only mount point,
  # not piped from the host, so the container and the tree cannot disagree
  # about what was applied.
  # MSYS_NO_PATHCONV is scoped to this one call and never exported: Git Bash
  # rewrites a container-side absolute path like /docker-entrypoint-initdb.d/...
  # into C:/Program Files/Git/... before docker.exe ever sees it (contract 04
  # §11). Exporting it globally breaks every other native tool on the host,
  # which is why scripts/lib/common.sh scopes it to its dc() function too.
  MSYS_NO_PATHCONV=1 "${DC[@]}" exec -T \
    -e PGPASSWORD="${WAREHOUSE_ADMIN_PASSWORD}" \
    warehouse-db \
    psql -v ON_ERROR_STOP=1 \
         -U "$WH_ADMIN" -d "$WH_DB" \
         -v wh_user="${WAREHOUSE_DB_USER:-warehouse}" \
         -v wh_password="${WAREHOUSE_DB_PASSWORD}" \
         -v loader_user="${WAREHOUSE_LOADER_USER:-warehouse_loader}" \
         -v loader_password="${WAREHOUSE_LOADER_PASSWORD}" \
         -v rls_user="${WAREHOUSE_RLS_USER:-warehouse_rls}" \
         -v rls_password="${WAREHOUSE_RLS_PASSWORD}" \
         -f "/docker-entrypoint-initdb.d/sql/$base"
done

echo "==> warehouse DDL applied"
