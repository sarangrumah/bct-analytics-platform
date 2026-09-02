#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Provision a new tenant.
#
#     scripts/tenant-provision.sh <slug> [--modules base,web] [--yes]
#
# "Multi-tenant by construction" (brief, Constraints): a tenant is one Odoo
# database, created by this script and never through the web database manager
# — which is why odoo.conf sets list_db = False.
#
# THIS SCRIPT IS THE PHASE 3 ONBOARDING SEAM. The block marked
# `PHASE 3 CDC HOOK` below is where warehouse onboarding attaches: publication,
# replication slot, dim_tenant row and the per-tenant masking salt. It is
# marked, ordered and commented so the extension is an edit in one place rather
# than a redesign.
# ---------------------------------------------------------------------------
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

SLUG=""
MODULES=""
ASSUME_YES=0

usage() {
    cat >&2 <<'USAGE'
usage: scripts/tenant-provision.sh <slug> [options]

  <slug>           Tenant identifier. Becomes the database name, the Odoo
                   dbfilter entry and (in Phase 3) the replication slot name.
                   Must match ^[a-z][a-z0-9_]{1,30}$ — no dashes, because
                   Postgres replication slot names forbid them.

  --modules LIST   Comma-separated modules to install (default:
                   $ODOO_INIT_MODULES, or "base,web").
  --yes            Do not prompt.
  -h, --help       This message.

example:
  scripts/tenant-provision.sh acme --modules base,web --yes
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --modules)   MODULES="${2:?--modules needs a value}"; shift 2 ;;
        --modules=*) MODULES="${1#*=}"; shift ;;
        --yes|-y)    ASSUME_YES=1; shift ;;
        -h|--help)   usage; exit 0 ;;
        -*)          die "unknown option: $1 (try --help)" ;;
        *)           [ -z "$SLUG" ] || die "only one slug may be given."; SLUG="$1"; shift ;;
    esac
done

[ -n "$SLUG" ] || { usage; die "a tenant slug is required."; }

require_docker
load_env
validate_slug "$SLUG"

MODULES="${MODULES:-${ODOO_INIT_MODULES:-base,web}}"
DB="$SLUG"

require_healthy postgres

if db_exists "$DB"; then
    die "database '$DB' already exists. Provisioning is not a repair tool — inspect it, or drop it deliberately first."
fi

log "provisioning tenant '$SLUG'"
info "database : $DB"
info "modules  : $MODULES"
info "reader   : $WAREHOUSE_READER_USER (SELECT + REPLICATION)"
confirm "Create tenant database '$DB'?"

# --- 1. database + Odoo schema ---------------------------------------------
# Odoo creates the database itself so that encoding and LC_COLLATE match what
# its registry expects (UTF8 / C, from template0).
log "[1/3] creating database and installing modules (this takes a minute)"
dc run --rm --no-deps -T odoo \
    odoo -d "$DB" -i "$MODULES" \
         --stop-after-init \
         --without-demo=True \
         --load-language=en_US

db_initialised "$DB" || die "odoo exited 0 but '$DB' has no ir_module_module."

# --- 2. baseline privileges -------------------------------------------------
log "[2/3] applying baseline privileges"
dc exec -T postgres psql -v ON_ERROR_STOP=1 --no-psqlrc --quiet \
    -U "$POSTGRES_USER" -d "$DB" \
    -v dbname="$DB" \
    -v reader="$WAREHOUSE_READER_USER" \
    -f - < "$REPO_ROOT/scripts/lib/database-baseline.sql"

# ===========================================================================
# [3/3] PHASE 3 CDC HOOK — the extension point, deliberately left inert.
#
# Phase 3 (Data Warehouse + Backend agents) extends provisioning so that a
# tenant arrives complete: database, publication, slot, warehouse dimension row
# and masking salt, in ONE run. Per docs/adr/0001-analytics-warehouse.md,
# "Multi-tenant": one publication and one replication slot per tenant database.
#
# Add it HERE, in this order, and nowhere else:
#
#   a) publication over the tables the CDC loader extracts. Not FOR ALL TABLES:
#      contract 01 requires `secret`-class columns to be structurally incapable
#      of landing, which means naming tables (and, on PG15+, columns).
#
#        psql_super "$DB" -c "CREATE PUBLICATION bct_cdc_${SLUG} FOR TABLE
#                             res_partner, res_users, sale_order, ...;"
#
#   b) replication slot. CREATE THIS LAST of the two: WAL retention begins the
#      moment the slot exists, and max_slot_wal_keep_size = 2GB starts counting
#      immediately. A slot created before a consumer is ready is exactly the
#      failure mode that cap exists to bound.
#
#        psql_super "$DB" -tAc "SELECT pg_create_logical_replication_slot(
#                                 'bct_slot_${SLUG}', 'pgoutput');"
#
#   c) warehouse dimension row + per-tenant masking salt:
#        WAREHOUSE_MASK_SALT_$(echo "$SLUG" | tr a-z A-Z) must exist in .env /
#        SOPS BEFORE the loader starts. Contract 01: an unclassified column is
#        a hard failure, never a silent default.
#
#   d) teardown counterpart in a future scripts/tenant-deprovision.sh:
#        pg_drop_replication_slot() BEFORE DROP DATABASE, or the drop blocks.
#
# No privilege change is needed at that point: warehouse_reader already holds
# REPLICATION (postgres/init/sql/20-roles.sql).
# ===========================================================================
log "[3/3] CDC onboarding: not implemented in Phase 1 (hook is marked in this script)"

# --- Report -----------------------------------------------------------------
cat >&2 <<REPORT

$(printf '%s' "${_C_GRN}")tenant '$SLUG' provisioned.${_C_OFF}

  database        $DB
  odoo owner      $POSTGRES_USER
  reader          $WAREHOUSE_READER_USER  (SELECT + REPLICATION, cannot write)

  MANUAL STEP — Odoo will not serve this database until dbfilter matches it.
  ODOO_DBFILTER is currently: ${ODOO_DBFILTER:-<unset>}

  Edit .env, then 'make restart':

      ODOO_DBFILTER=^(${ODOO_DB_NAME}|${SLUG})\$

  This is deliberately not automated. Widening dbfilter changes which tenants
  are reachable over HTTP, and that should be a reviewed edit, not a side
  effect of running a provisioning script.

  Backup with:  make tenant-backup TENANT=$SLUG
REPORT
