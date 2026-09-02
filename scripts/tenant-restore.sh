#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Restore one tenant: DATABASE **and** FILESTORE.
#
#     scripts/tenant-restore.sh <slug> <backup-dir> [--yes] [--into OTHER_SLUG]
#
# This is destructive: the target database is dropped and recreated. It refuses
# to run non-interactively without --yes, and it verifies SHA256SUMS before
# touching anything — restoring a truncated dump over a live database is worse
# than not restoring at all.
#
# --into lets you restore a backup as a DIFFERENT tenant, which is how you test
# a restore without risking production. Use it.
# ---------------------------------------------------------------------------
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

SLUG=""
SRC=""
TARGET=""
ASSUME_YES=0

usage() {
    cat >&2 <<'USAGE'
usage: scripts/tenant-restore.sh <slug> <backup-dir> [options]

  <slug>          Tenant the backup was taken from.
  <backup-dir>    Directory containing database.dump, filestore.tar.gz,
                  manifest.json and SHA256SUMS.

  --into SLUG     Restore into a different tenant (a copy). Recommended for
                  rehearsals: it never touches the original.
  --yes           Do not prompt. Required in a non-interactive shell.
  -h, --help      This message.

example:
  scripts/tenant-restore.sh bct backups/bct/20260831T041500Z --into bct_restore_test --yes
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --into)    TARGET="${2:?--into needs a value}"; shift 2 ;;
        --into=*)  TARGET="${1#*=}"; shift ;;
        --yes|-y)  ASSUME_YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        -*)        die "unknown option: $1 (try --help)" ;;
        *)
            if   [ -z "$SLUG" ]; then SLUG="$1"
            elif [ -z "$SRC"  ]; then SRC="$1"
            else die "unexpected argument: $1"
            fi
            shift ;;
    esac
done

[ -n "$SLUG" ] && [ -n "$SRC" ] || { usage; die "both <slug> and <backup-dir> are required."; }

require_docker
load_env
validate_slug "$SLUG"

TARGET="${TARGET:-$SLUG}"
validate_slug "$TARGET"

case "$SRC" in /*|[A-Za-z]:*) ;; *) SRC="$REPO_ROOT/${SRC#./}" ;; esac
[ -d "$SRC" ] || die "backup directory not found: $SRC"

DUMP="$SRC/database.dump"
FILESTORE="$SRC/filestore.tar.gz"
[ -f "$DUMP" ]      || die "missing $DUMP"
[ -f "$FILESTORE" ] || die "missing $FILESTORE"

require_healthy postgres odoo

# --- verify before destroying anything --------------------------------------
log "[1/6] verifying backup integrity"
if [ -f "$SRC/SHA256SUMS" ]; then
    ( cd "$SRC" && sha256sum -c SHA256SUMS ) >&2 \
        || die "checksum mismatch — this backup is corrupt. Refusing to restore."
else
    warn "no SHA256SUMS in $SRC; integrity NOT verified."
fi

if [ -f "$SRC/manifest.json" ]; then
    python3 - "$SRC/manifest.json" "$SLUG" <<'PY' >&2
import json, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
expected = sys.argv[2]
found = manifest.get("tenant_slug")
print(f"    manifest: tenant={found} taken_at={manifest.get('taken_at_utc')} "
      f"components={','.join(manifest.get('components', []))}")
if found != expected:
    print(f"    WARNING manifest says tenant '{found}' but '{expected}' was requested.")
PY
fi

cat >&2 <<BANNER

  ${_C_YEL}DESTRUCTIVE${_C_OFF}
  Target database '$TARGET' will be DROPPED and recreated from:
      $SRC
  Its filestore at /var/lib/odoo/filestore/$TARGET will be replaced.

BANNER
confirm "Proceed?"

# --- 2. stop odoo -----------------------------------------------------------
# DROP DATABASE fails while any session is connected, and Odoo holds a pool.
# Stopping the service is cleaner and more honest than pg_terminate_backend in
# a loop, which races against Odoo's own reconnect.
log "[2/6] stopping odoo (it holds connections to the target database)"
dc stop odoo >/dev/null 2>&1 || true

# --- 3. recreate the database ----------------------------------------------
log "[3/6] recreating database '$TARGET'"
psql_super "$POSTGRES_DB" -c "DROP DATABASE IF EXISTS \"$TARGET\" WITH (FORCE);" >/dev/null
# Same shape Odoo itself uses: template0 with LC_COLLATE 'C'. A different
# collation makes Odoo's registry load fail in ways that look like data
# corruption.
psql_super "$POSTGRES_DB" -c \
    "CREATE DATABASE \"$TARGET\" OWNER \"$POSTGRES_USER\" ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0;" >/dev/null

# --- 4. restore the database ------------------------------------------------
log "[4/6] pg_restore"
# --no-owner --no-acl: the dump was taken the same way. Privileges come from
# scripts/lib/database-baseline.sql in step 6, so a restore can never
# reintroduce a stale grant.
# --exit-on-error so a partial restore fails loudly instead of leaving a
# half-populated database that looks fine until a report runs.
dc exec -T postgres pg_restore \
    -U "$POSTGRES_USER" -d "$TARGET" \
    --no-owner --no-acl --exit-on-error \
    < "$DUMP"

# --- 5. restore the filestore -----------------------------------------------
log "[5/6] restoring filestore"
# Started with --no-deps so odoo is up for the extraction but does not reopen a
# pool against a database that is mid-restore.
dc up -d --no-deps odoo >/dev/null
wait_healthy odoo || die "odoo did not come back healthy; filestore not restored."

dc exec -T odoo sh -c "rm -rf '/var/lib/odoo/filestore/$TARGET'"
# The archive contains a top-level directory named after the SOURCE database.
# --strip-components=1 plus an explicit -C into the target directory is what
# makes `--into` work for a rename.
dc exec -T odoo sh -c "mkdir -p '/var/lib/odoo/filestore/$TARGET'"
dc exec -T odoo sh -c "tar -xzf - -C '/var/lib/odoo/filestore/$TARGET' --strip-components=1" < "$FILESTORE" \
    || warn "filestore archive was empty or had no top-level directory; continuing."

# --- 6. re-apply privileges -------------------------------------------------
log "[6/6] re-applying baseline privileges"
dc exec -T postgres psql -v ON_ERROR_STOP=1 --no-psqlrc --quiet \
    -U "$POSTGRES_USER" -d "$TARGET" \
    -v dbname="$TARGET" \
    -v reader="$WAREHOUSE_READER_USER" \
    -f - < "$REPO_ROOT/scripts/lib/database-baseline.sql"

dc restart odoo >/dev/null
wait_healthy odoo || warn "odoo is not healthy after restart — check 'make logs'."

log "restore complete: '$TARGET' from $SRC"
if [ "$TARGET" != "$SLUG" ]; then
    info "restored as '$TARGET'. Odoo will not serve it until ODOO_DBFILTER matches:"
    info "    ODOO_DBFILTER=^(${ODOO_DB_NAME}|${TARGET})\$"
fi
