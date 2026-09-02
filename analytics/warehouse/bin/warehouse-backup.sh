#!/usr/bin/env bash
# ===========================================================================
# warehouse-backup.sh — logical dump of the analytics warehouse.
#
#   bash analytics/warehouse/bin/warehouse-backup.sh [--out DIR]
#   bash analytics/warehouse/bin/warehouse-backup.sh --restore DIR
#
# Follows scripts/tenant-backup.sh's conventions exactly, because one runbook
# is the whole point of ADR 0001 choosing Postgres: same layout, same
# pg_dump --format=custom --compress=9, same manifest.json,
# same SHA256SUMS verified BEFORE anything is dropped.
#
# WHAT IS AND IS NOT BACKED UP, and why the difference matters here more than
# it does for the ERP:
#
#   raw.*        BACKED UP. It is the append-only landing zone and it is the
#                only copy of history that Odoo no longer has - a row updated
#                five times in Odoo leaves one current value there and five
#                versions here.
#   marts, staging, snapshots   BACKED UP, but they are DERIVED: `dbt build`
#                reproduces marts and staging from raw exactly. The snapshots
#                schema is the exception that justifies backing the rest up
#                anyway - SCD2 history is NOT reproducible from raw once the
#                landing zone has been trimmed, because a snapshot records what
#                the world looked like when it ran.
#   warehouse.*  BACKED UP. column_policy is re-derivable from custom_pdp_core,
#                but pipeline_state is not: losing it means the CDC consumer
#                does not know where it stopped.
#
# NO FILESTORE HALF. tenant-backup.sh insists on both halves because an Odoo
# database without its filestore restores to broken attachments. The warehouse
# has no filestore: `ir_attachment` is never replicated (custom_pdp_core §7 -
# an attachment can be anything at all, a scanned KTP included, and there is no
# classification that would make it safe). So one file is a COMPLETE backup
# here, and that is a property of the design rather than an omission.
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
OUT_ROOT="${BACKUP_DIR:-./backups}/warehouse"
MODE=backup
RESTORE_DIR=""

while [ $# -gt 0 ]; do
  case "$1" in
    --out)     OUT_ROOT="$2"; shift 2 ;;
    --restore) MODE=restore; RESTORE_DIR="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

log() { printf '  %s\n' "$*" >&2; }

# --- restore ---------------------------------------------------------------
if [ "$MODE" = restore ]; then
  [ -d "$RESTORE_DIR" ] || { echo "no such backup directory: $RESTORE_DIR" >&2; exit 1; }
  log "[1/4] verifying SHA256SUMS BEFORE touching the database"
  ( cd "$RESTORE_DIR" && sha256sum -c SHA256SUMS ) >&2

  log "[2/4] restoring into database ${WH_DB}"
  # --clean --if-exists rather than DROP DATABASE: dbt, the CDC loader and the
  # semantic API all hold pooled connections, and DROP DATABASE fails while any
  # session is attached. This restores in place, which is what a real recovery
  # would do with services running.
  #
  # NOTE the ABSENCE of --no-owner, and it is deliberate. See the pg_dump call
  # below for the full reason; the short version is that ownership is a
  # security control here, not metadata.
  MSYS_NO_PATHCONV=1 "${DC[@]}" exec -T -e PGPASSWORD="${WAREHOUSE_ADMIN_PASSWORD}" warehouse-db \
    pg_restore --clean --if-exists \
               -U "$WH_ADMIN" -d "$WH_DB" < "$RESTORE_DIR/database.dump"

  log "[3/4] reasserting ownership on the non-superuser role, then re-applying the tracked DDL"
  # TWO THINGS A DUMP DOES NOT CARRY, both of which the warehouse needs.
  #
  # 1. OWNERSHIP OF ANYTHING THE DUMP DID NOT NAME AN OWNER FOR. pg_restore
  #    assigns those to the RESTORING role, which here is warehouse_admin, a
  #    SUPERUSER. A superuser bypasses row security unconditionally, so a
  #    restore that leaves the marts owned by one has silently deleted the
  #    tenant boundary - with nothing erroring and every query still returning
  #    rows. This was not theoretical: an earlier version of this script passed
  #    --no-owner and produced exactly that, all 41 tables and five schemas
  #    owned by warehouse_admin, with `warehouse` getting "permission denied
  #    for schema marts" on its own warehouse.
  #
  # 2. ROLE PASSWORDS AND ALTER ROLE SETTINGS. Without warehouse-apply.sh the
  #    restored warehouse has its tables and none of the privilege separation
  #    that makes RLS mean anything.
  #
  # Identity-linked sequences are skipped: they follow their table, and
  # ALTER SEQUENCE OWNER on one errors with "is linked to table".
  MSYS_NO_PATHCONV=1 "${DC[@]}" exec -T -e PGPASSWORD="${WAREHOUSE_ADMIN_PASSWORD}" warehouse-db \
    psql -v ON_ERROR_STOP=1 -q -U "$WH_ADMIN" -d "$WH_DB" \
         -v wh="${WAREHOUSE_DB_USER:-warehouse}" <<'OWNERSQL'
-- psql substitutes :'wh' at the top level but NOT inside a dollar-quoted
-- block, which is why the role name travels through set_config rather than
-- being interpolated into the DO body.
SELECT set_config('warehouse.reown_target', :'wh', false);
DO $reown$
DECLARE
  r record;
  owner text := current_setting('warehouse.reown_target');
BEGIN
  FOR r IN SELECT nspname FROM pg_namespace
            WHERE nspname IN ('raw','staging','marts','warehouse','snapshots')
               OR nspname LIKE 'src\_%' LOOP
    EXECUTE format('ALTER SCHEMA %I OWNER TO %I', r.nspname, owner);
  END LOOP;

  FOR r IN SELECT n.nspname, c.relname, c.relkind FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE (n.nspname IN ('raw','staging','marts','warehouse','snapshots')
                  OR n.nspname LIKE 'src\_%')
             AND c.relkind IN ('r','v','m','S','p','f')
             AND (c.relkind <> 'S' OR NOT EXISTS (
                   SELECT 1 FROM pg_depend d
                    WHERE d.objid = c.oid
                      AND d.classid = 'pg_class'::regclass
                      AND d.deptype IN ('a','i'))) LOOP
    EXECUTE format('ALTER %s %I.%I OWNER TO %I',
                   CASE r.relkind
                     WHEN 'S' THEN 'SEQUENCE'
                     WHEN 'v' THEN 'VIEW'
                     WHEN 'm' THEN 'MATERIALIZED VIEW'
                     WHEN 'f' THEN 'FOREIGN TABLE'
                     ELSE 'TABLE' END,
                   r.nspname, r.relname, owner);
  END LOOP;

  FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname IN ('raw','staging','marts','warehouse','snapshots') LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO %I', r.sig, owner);
  END LOOP;

  RAISE NOTICE 'ownership reasserted on %', owner;
END
$reown$;
OWNERSQL

  bash analytics/warehouse/bin/warehouse-apply.sh >/dev/null

  log "[4/4] asserting the tenant boundary survived the restore"
  # A restore that looks like it worked and has quietly removed RLS is the
  # worst outcome available here, so it is asserted rather than assumed.
  MSYS_NO_PATHCONV=1 "${DC[@]}" exec -T -e PGPASSWORD="${WAREHOUSE_ADMIN_PASSWORD}" warehouse-db \
    psql -v ON_ERROR_STOP=1 -U "$WH_ADMIN" -d "$WH_DB" <<'CHECKSQL'
DO $chk$
DECLARE bad int;
BEGIN
  SELECT count(*) INTO bad
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_roles ro ON ro.oid = c.relowner
   WHERE n.nspname = 'marts' AND c.relkind = 'r'
     AND (ro.rolsuper OR ro.rolbypassrls);
  IF bad > 0 THEN
    RAISE EXCEPTION
      'RESTORE FAILED THE SECURITY CHECK: % mart(s) are owned by a role that bypasses row '
      'security. The tenant boundary is gone.', bad;
  END IF;

  SELECT count(*) INTO bad
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'marts' AND c.relkind = 'r'
     AND NOT (c.relrowsecurity AND c.relforcerowsecurity);
  IF bad > 0 THEN
    RAISE EXCEPTION 'RESTORE FAILED THE SECURITY CHECK: % mart(s) lack ENABLE+FORCE RLS.', bad;
  END IF;

  RAISE NOTICE 'restore security check PASSED: every mart is owned by a non-superuser and has FORCE RLS';
END
$chk$;
CHECKSQL

  log "restore complete. Run 'make dbt-run' to rebuild derived models."
  exit 0
fi

# --- backup ----------------------------------------------------------------
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$OUT_ROOT/$STAMP"
mkdir -p "$DEST"

# NOTE the deviation from scripts/tenant-backup.sh, which passes
# --no-owner --no-acl. That is right for Odoo, where a restore may target a
# database whose role names differ. It is WRONG here: this warehouse's roles
# are created by tracked DDL under fixed names, and ownership is LOAD-BEARING
# rather than metadata - `warehouse` must own the marts, because it is
# NOSUPERUSER NOBYPASSRLS and that is the only reason FORCE ROW LEVEL
# SECURITY means anything. Ownership therefore travels with the dump.
log "[1/3] pg_dump (custom format, compress 9)"
MSYS_NO_PATHCONV=1 "${DC[@]}" exec -T -e PGPASSWORD="${WAREHOUSE_ADMIN_PASSWORD}" warehouse-db \
  pg_dump --format=custom --compress=9 \
          -U "$WH_ADMIN" -d "$WH_DB" > "$DEST/database.dump"
[ -s "$DEST/database.dump" ] || { echo "pg_dump produced an empty file." >&2; exit 1; }

log "[2/3] manifest"
DEST="$DEST" WH_DB="$WH_DB" STAMP="$STAMP" python3 - <<'PY'
import hashlib, json, os, subprocess, sys

dest = os.environ["DEST"]
entries = {}
for name in sorted(os.listdir(dest)):
    path = os.path.join(dest, name)
    if not os.path.isfile(path) or name in ("manifest.json", "SHA256SUMS"):
        continue
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    entries[name] = {"sha256": h.hexdigest(), "bytes": os.path.getsize(path)}


def git(*args):
    try:
        return subprocess.check_output(["git", *args], text=True).strip()
    except Exception:
        return None


manifest = {
    "kind": "analytics-warehouse",
    "database": os.environ["WH_DB"],
    "taken_at_utc": os.environ["STAMP"],
    "files": entries,
    "total_bytes": sum(e["bytes"] for e in entries.values()),
    "git_commit": git("rev-parse", "HEAD"),
    "git_branch": git("rev-parse", "--abbrev-ref", "HEAD"),
    # Recorded because a warehouse dump is only meaningful against the dbt
    # project that built it: restoring this dump and running a different
    # revision's models produces marts that match neither.
    "note": "Derived schemas (staging, marts) are reproducible from raw by `dbt build`; "
            "snapshots and warehouse.pipeline_state are NOT.",
}
with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8", newline="\n") as fh:
    json.dump(manifest, fh, indent=2, sort_keys=True)
with open(os.path.join(dest, "SHA256SUMS"), "w", encoding="utf-8", newline="\n") as fh:
    for name, meta in entries.items():
        fh.write(f"{meta['sha256']}  {name}\n")
print(f"    {manifest['total_bytes'] / 1024 / 1024:.1f} MiB", file=sys.stderr)
PY

log "[3/3] verifying the checksums just written"
( cd "$DEST" && sha256sum -c SHA256SUMS ) >&2

log "backup complete: $DEST"
