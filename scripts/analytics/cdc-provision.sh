#!/usr/bin/env bash
# Create the per-tenant publication for the CDC pipeline.
#
# Runs as the `odoo` role, not as `warehouse_reader`, and that is the point: CREATE PUBLICATION
# requires ownership of the tables, and `warehouse_reader` correctly holds only SELECT +
# REPLICATION (contract 04). The loader therefore cannot create its own publication, which is the
# same property that makes "no write path from the warehouse into Odoo" structural rather than
# a policy (anti-pattern 7.10).
#
# Order matters and is enforced here: publication FIRST, slot second. WAL retention starts the
# instant a slot exists and the 2 GB cap starts counting immediately, so a slot created before its
# consumer is ready is exactly the failure the cap exists to bound. The slot is created by the
# consumer itself, at the end of its startup checks.
#
# The publication carries a PER-TABLE COLUMN LIST built from warehouse.column_policy. That is the
# structural control behind contract 01's "secret is dropped at extraction": a column absent from
# the list is never put on the wire by Postgres, so no bug in the loader can land it.
#
# Usage: scripts/analytics/cdc-provision.sh [--slug bct] [--dry-run]
set -euo pipefail

SLUG=""
DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --slug) SLUG="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "usage: $0 [--slug SLUG] [--dry-run]" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
set -a; . "$ROOT/.env"; set +a

SLUG="${SLUG:-${ODOO_DB_NAME:-bct}}"
# Contract 04: slot names forbid dashes, so slugs are ^[a-z][a-z0-9_]{1,30}$.
if ! printf '%s' "$SLUG" | grep -Eq '^[a-z][a-z0-9_]{1,30}$'; then
  echo "invalid tenant slug '$SLUG': must match ^[a-z][a-z0-9_]{1,30}\$ (no dashes -- Postgres" >&2
  echo "replication slot names forbid them)" >&2
  exit 2
fi
PUBLICATION="bct_cdc_${SLUG}"

echo "==> generating the publication column list from warehouse.column_policy"
SQL="$(docker run --rm --network odoo19-bct_bct \
  -e WAREHOUSE_READER_PASSWORD -e WAREHOUSE_LOADER_PASSWORD -e WAREHOUSE_DB -e WAREHOUSE_LOADER_USER \
  -e WAREHOUSE_MASK_SALT_DEFAULT -e WAREHOUSE_MASK_SALT_BCT \
  -e CDC_TENANT_DB="$SLUG" -e CDC_TENANT_SLUG="$SLUG" \
  -e CDC_WAREHOUSE_HOST="${CDC_WAREHOUSE_HOST:-warehouse-db}" \
  -e CDC_VERIFY_DIGEST_SPEC=0 \
  odoo19-bct-cdc:local --print-publication-sql --log-level WARNING)"

if [ "$DRY_RUN" = "1" ]; then
  printf '%s\n' "$SQL"
  exit 0
fi

echo "==> applying to database $SLUG as the odoo role"
printf 'DROP PUBLICATION IF EXISTS %s;\n%s\n' "$PUBLICATION" "$SQL" \
  | docker compose -p odoo19-bct exec -T postgres \
      psql -U odoo -d "$SLUG" -v ON_ERROR_STOP=1

echo "==> publication $PUBLICATION created. Slot bct_slot_${SLUG} is created by the consumer."
docker compose -p odoo19-bct exec -T postgres psql -U odoo -d "$SLUG" -c \
  "select pubname, pubinsert, pubupdate, pubdelete, pubtruncate from pg_publication where pubname='${PUBLICATION}'"
