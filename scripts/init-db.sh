#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Initialise the default Odoo database, idempotently.
#
#     scripts/init-db.sh [--modules base,web] [--force]
#
# Called automatically by `make up-dev`, so that a clean checkout reaches a
# working /web/login in two commands with no manual step in between.
#
# Idempotency matters here more than anywhere else: up-dev runs this on every
# invocation. If the database is already initialised it is a no-op and costs
# one query.
# ---------------------------------------------------------------------------
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

MODULES=""
FORCE=0

usage() {
    cat >&2 <<'USAGE'
usage: scripts/init-db.sh [options]

  --modules LIST   Comma-separated modules to install (default: $ODOO_INIT_MODULES
                   from .env, or "base,web").
  --force          Re-run the install even if the database already has an Odoo
                   schema. Does NOT drop data; it is `odoo -u`, not a reset.
  -h, --help       This message.
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --modules) MODULES="${2:?--modules needs a value}"; shift 2 ;;
        --modules=*) MODULES="${1#*=}"; shift ;;
        --force) FORCE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1 (try --help)" ;;
    esac
done

require_docker
load_env

DB="$ODOO_DB_NAME"
MODULES="${MODULES:-${ODOO_INIT_MODULES:-base,web}}"

require_healthy postgres

if db_initialised "$DB" && [ "$FORCE" -eq 0 ]; then
    log "database '$DB' already carries an Odoo schema — nothing to do."
    info "re-run with --force to update modules: $MODULES"
else
    if db_initialised "$DB"; then
        # -u only UPGRADES modules that are already installed; on a module that
        # is merely present in addons_path it is a silent no-op. Using -u alone
        # here meant `make install-modules` could not install anything - it
        # reported success and changed nothing, which is how five custom modules
        # stayed `uninstalled` through a recovery that looked clean.
        #
        # So split the list: -i for what is not installed yet, -u for what is.
        # Odoo accepts both flags in one run.
        WANT="$(printf '%s' "$MODULES" | tr ',' ' ')"
        PRESENT="$(psql_super "$DB" -tAc "SELECT string_agg(name, ' ') FROM ir_module_module WHERE state = 'installed'" 2>/dev/null || true)"
        TO_INSTALL=""; TO_UPGRADE=""
        for m in $WANT; do
            case " $PRESENT " in
                *" $m "*) TO_UPGRADE="${TO_UPGRADE:+$TO_UPGRADE,}$m" ;;
                *)        TO_INSTALL="${TO_INSTALL:+$TO_INSTALL,}$m" ;;
            esac
        done
        ODOO_ARGS=(-d "$DB")
        [ -n "$TO_INSTALL" ] && { log "installing (not yet present): $TO_INSTALL"; ODOO_ARGS+=(-i "$TO_INSTALL"); }
        [ -n "$TO_UPGRADE" ] && { log "upgrading (already installed): $TO_UPGRADE"; ODOO_ARGS+=(-u "$TO_UPGRADE"); }
    else
        log "initialising database '$DB' with modules: $MODULES"
        # Odoo creates the database itself, with the encoding and LC_COLLATE it
        # requires (UTF8 / C from template0). Creating it by hand with createdb
        # risks a collation Odoo then rejects at registry load.
        ODOO_ARGS=(-d "$DB" -i "$MODULES")
    fi

    # ------------------------------------------------------------------
    # Stop the running server first, if there is one.
    #
    # A module install/update runs DDL — `ALTER TABLE res_users ...` and
    # friends. The live server holds a connection pool and a loaded registry
    # against the same tables, and the two deadlock:
    #
    #   ERROR: deadlock detected
    #   bad query: ALTER TABLE "res_users" ALTER COLUMN "notification_type" DROP NOT NULL
    #   CRITICAL: Failed to initialize database `bct`.
    #
    # `make up-dev` never hits this, because it runs init BEFORE starting odoo.
    # `make install-modules` on a running stack hits it every time. Found by
    # running it against real modules, not by reasoning about it.
    # ------------------------------------------------------------------
    odoo_was_running=0
    if [ "$(health_of odoo)" != "absent" ] && \
       [ "$(docker inspect -f '{{.State.Running}}' "$(container_of odoo)" 2>/dev/null)" = "true" ]; then
        odoo_was_running=1
        log "stopping odoo: DDL against a database its workers hold will deadlock"
        dc stop odoo >/dev/null
    fi

    # --no-deps: postgres and redis are already up and healthy (checked above);
    # without it, `run` would start a second dependency chain.
    # --rm: the init container is disposable.
    # A separate one-off container rather than `exec` into the running server,
    # so this also works before the odoo service has ever started — which is
    # exactly the ordering `make up-dev` relies on.
    set +e
    dc run --rm --no-deps -T odoo \
        odoo "${ODOO_ARGS[@]}" \
             --stop-after-init \
             --without-demo=True \
             --load-language=en_US
    odoo_rc=$?
    set -e

    if [ "$odoo_was_running" -eq 1 ]; then
        log "restarting odoo"
        dc up -d odoo >/dev/null
        wait_healthy odoo || warn "odoo did not come back healthy — check 'make logs'."
    fi

    [ "$odoo_rc" -eq 0 ] || die "odoo exited $odoo_rc during '${ODOO_ARGS[*]}'. See the traceback above."
    db_initialised "$DB" || die "odoo exited 0 but '$DB' has no ir_module_module — check 'make logs'."

    # Assert the outcome, not the exit code. `odoo -u` on an absent module exits
    # 0 having done nothing; without this check that reads as success.
    NOT_INSTALLED=""
    for m in $(printf '%s' "$MODULES" | tr ',' ' '); do
        st="$(psql_super "$DB" -tAc "SELECT state FROM ir_module_module WHERE name = '$m'" 2>/dev/null || true)"
        [ "$st" = "installed" ] || NOT_INSTALLED="${NOT_INSTALLED:+$NOT_INSTALLED }$m(${st:-absent})"
    done
    [ -z "$NOT_INSTALLED" ] || die "odoo exited 0 but these are not installed: $NOT_INSTALLED"
    log "database '$DB' modules applied: $MODULES"
fi

# ---------------------------------------------------------------------------
# Baseline privileges. Applied on EVERY run, not only on first creation:
# installing a module creates new tables, and although ALTER DEFAULT PRIVILEGES
# covers them, re-applying makes the state converge even if someone installed a
# module by hand from the UI.
# ---------------------------------------------------------------------------
log "applying baseline privileges to '$DB' (warehouse_reader: SELECT only)"
dc exec -T postgres psql -v ON_ERROR_STOP=1 --no-psqlrc --quiet \
    -U "$POSTGRES_USER" -d "$DB" \
    -v dbname="$DB" \
    -v reader="$WAREHOUSE_READER_USER" \
    -f - < "$REPO_ROOT/scripts/lib/database-baseline.sql"

log "done. Odoo database '$DB' is ready."
info "login page: http://${BIND_ADDRESS:-127.0.0.1}:${ODOO_HOST_HTTP_PORT:-38069}/web/login"
