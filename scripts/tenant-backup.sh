#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Back up one tenant: DATABASE **and** FILESTORE.
#
#     scripts/tenant-backup.sh <slug> [--out DIR] [--keep-days N]
#
# Both halves, always. An Odoo backup that contains only the database restores
# to a system whose every attachment, logo, product image and generated PDF is
# a broken link — ir_attachment rows point at files under
# /var/lib/odoo/filestore/<db>/ that the dump does not contain. This is the
# single most common way an Odoo "backup" turns out not to be one.
#
# Output layout:
#     backups/<slug>/<UTC timestamp>/
#         database.dump     pg_dump -Fc  (compressed, restores with pg_restore)
#         filestore.tar.gz  tar of /var/lib/odoo/filestore/<db>
#         manifest.json     what was taken, from where, and its size
#         SHA256SUMS        integrity, verified by tenant-restore.sh
# ---------------------------------------------------------------------------
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

SLUG=""
OUT_ROOT=""
KEEP_DAYS=""

usage() {
    cat >&2 <<'USAGE'
usage: scripts/tenant-backup.sh <slug> [options]

  --out DIR        Backup root (default: $BACKUP_DIR from .env, or ./backups).
  --keep-days N    Delete backups for this tenant older than N days
                   (default: $BACKUP_RETENTION_DAYS, or 14). 0 disables pruning.
  -h, --help       This message.
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --out)         OUT_ROOT="${2:?--out needs a value}"; shift 2 ;;
        --out=*)       OUT_ROOT="${1#*=}"; shift ;;
        --keep-days)   KEEP_DAYS="${2:?--keep-days needs a value}"; shift 2 ;;
        --keep-days=*) KEEP_DAYS="${1#*=}"; shift ;;
        -h|--help)     usage; exit 0 ;;
        -*)            die "unknown option: $1 (try --help)" ;;
        *)             [ -z "$SLUG" ] || die "only one slug may be given."; SLUG="$1"; shift ;;
    esac
done

[ -n "$SLUG" ] || { usage; die "a tenant slug is required."; }

require_docker
load_env
validate_slug "$SLUG"

DB="$SLUG"
OUT_ROOT="${OUT_ROOT:-$BACKUP_DIR}"
case "$OUT_ROOT" in /*|[A-Za-z]:*) ;; *) OUT_ROOT="$REPO_ROOT/${OUT_ROOT#./}" ;; esac
KEEP_DAYS="${KEEP_DAYS:-$BACKUP_RETENTION_DAYS}"

require_healthy postgres odoo
db_exists "$DB" || die "database '$DB' does not exist."

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$OUT_ROOT/$SLUG/$STAMP"
mkdir -p "$DEST"

log "backing up tenant '$SLUG' -> $DEST"

# --- 1. database ------------------------------------------------------------
# -Fc (custom format) rather than plain SQL: it is compressed, and pg_restore
# can then be selective and parallel on the way back in.
# --no-owner / --no-acl: privileges are re-applied from
# scripts/lib/database-baseline.sql on restore, so the dump never carries a
# stale grant for a role that may have been renamed or rotated.
log "[1/4] pg_dump (custom format)"
dc exec -T postgres pg_dump \
    -U "$POSTGRES_USER" -d "$DB" \
    --format=custom --compress=9 --no-owner --no-acl \
    > "$DEST/database.dump"

[ -s "$DEST/database.dump" ] || die "pg_dump produced an empty file."

# --- 2. filestore -----------------------------------------------------------
# Streamed as a tar over stdout: no temporary file inside the container, so a
# large filestore cannot fill the container's writable layer.
# `|| true` on the tar is deliberate for the empty case only — a brand new
# database has no filestore directory at all, and that is not an error.
log "[2/4] filestore tar"
if dc exec -T odoo test -d "/var/lib/odoo/filestore/$DB" 2>/dev/null; then
    dc exec -T odoo tar -C /var/lib/odoo/filestore -czf - "$DB" > "$DEST/filestore.tar.gz"
    [ -s "$DEST/filestore.tar.gz" ] || die "filestore tar produced an empty file."
else
    warn "no filestore directory for '$DB' yet (a fresh database has none); writing an empty archive."
    dc exec -T odoo tar -C /var/lib/odoo -czf - --files-from /dev/null > "$DEST/filestore.tar.gz"
fi

# --- 3. manifest ------------------------------------------------------------
# python3, not jq: jq is not installed on the target host and never will be a
# dependency of this repository.
log "[3/4] manifest"
python3 - "$DEST" "$SLUG" "$DB" "$STAMP" <<'PY'
import hashlib, json, os, subprocess, sys

dest, slug, db, stamp = sys.argv[1:5]

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

files = {}
for name in ("database.dump", "filestore.tar.gz"):
    p = os.path.join(dest, name)
    files[name] = {"bytes": os.path.getsize(p), "sha256": sha256(p)}

def git(*args):
    try:
        return subprocess.check_output(("git",) + args, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None

manifest = {
    "schema_version": 1,
    "tenant_slug": slug,
    "database": db,
    "taken_at_utc": stamp,
    "components": ["database", "filestore"],
    "files": files,
    "source": {
        "compose_project": os.environ.get("COMPOSE_PROJECT_NAME"),
        "git_commit": git("rev-parse", "HEAD"),
        "git_branch": git("rev-parse", "--abbrev-ref", "HEAD"),
    },
    "restore_with": f"scripts/tenant-restore.sh {slug} {dest}",
}

with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8", newline="\n") as fh:
    json.dump(manifest, fh, indent=2, sort_keys=True)
    fh.write("\n")

with open(os.path.join(dest, "SHA256SUMS"), "w", encoding="utf-8", newline="\n") as fh:
    for name, meta in sorted(files.items()):
        fh.write(f"{meta['sha256']}  {name}\n")

total = sum(m["bytes"] for m in files.values())
print(f"    manifest written; {total / 1024 / 1024:.1f} MiB total", file=sys.stderr)
PY

# --- 4. prune ---------------------------------------------------------------
log "[4/4] retention"
if [ "${KEEP_DAYS:-0}" -gt 0 ] 2>/dev/null; then
    # -mindepth/-maxdepth 1 so this can only ever match this tenant's dated
    # directories, never the tenant directory itself and never anything above
    # it. Deleting backups is the one operation here with no undo.
    pruned=0
    while IFS= read -r old; do
        [ -n "$old" ] || continue
        info "pruning $(basename "$old") (older than ${KEEP_DAYS}d)"
        rm -rf -- "$old"
        pruned=$((pruned + 1))
    done < <(find "$OUT_ROOT/$SLUG" -mindepth 1 -maxdepth 1 -type d -mtime "+$KEEP_DAYS" 2>/dev/null || true)
    info "pruned $pruned old backup(s); keeping ${KEEP_DAYS} days"
else
    info "retention disabled (--keep-days 0)"
fi

log "backup complete: $DEST"
ls -la "$DEST" >&2
