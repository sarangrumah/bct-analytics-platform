#!/bin/bash
# ---------------------------------------------------------------------------
# First-boot cluster initialisation. Runs ONCE, when $PGDATA is empty, from
# docker-entrypoint.sh's docker_process_init_files().
#
# Why a .sh wrapper instead of plain .sql files?
#   The postgres entrypoint pipes *.sql straight into psql with no -v flags,
#   and psql cannot read environment variables. Role passwords come from the
#   environment, so they have to be injected as psql variables. This wrapper
#   does that, then applies the real DDL from init/sql/.
#
#   Files in init/sql/ are NOT picked up by the entrypoint itself: it iterates
#   /docker-entrypoint-initdb.d/* and skips directories ("ignoring ..."), which
#   is exactly the behaviour relied on here.
#
# The entrypoint runs this with `source` when the exec bit is absent (it is
# absent on Windows bind mounts), so: no `exit`, no `set -e` teardown that
# would kill the parent. Errors are surfaced by ON_ERROR_STOP inside psql and
# checked explicitly.
# ---------------------------------------------------------------------------

bct_init() {
    set -u

    local sql_dir=/docker-entrypoint-initdb.d/sql
    local reader_user="${WAREHOUSE_READER_USER:-warehouse_reader}"
    local reader_pw="${WAREHOUSE_READER_PASSWORD:-}"
    local exporter_user="${POSTGRES_EXPORTER_USER:-metrics_exporter}"
    local exporter_pw="${POSTGRES_EXPORTER_PASSWORD:-}"

    if [ -z "$reader_pw" ]; then
        echo "bct-init: FATAL WAREHOUSE_READER_PASSWORD is empty." >&2
        echo "bct-init: run 'make dev-bootstrap' to generate .env from .env.example." >&2
        return 1
    fi
    if [ "$reader_pw" = "changeme" ]; then
        echo "bct-init: FATAL WAREHOUSE_READER_PASSWORD is still the literal placeholder 'changeme'." >&2
        return 1
    fi
    if [ -z "$exporter_pw" ] || [ "$exporter_pw" = "changeme" ]; then
        echo "bct-init: FATAL POSTGRES_EXPORTER_PASSWORD is empty or still 'changeme'." >&2
        return 1
    fi

    local f
    for f in "$sql_dir"/*.sql; do
        [ -e "$f" ] || continue
        echo "bct-init: applying $f"
        psql \
            --username "$POSTGRES_USER" \
            --dbname "${POSTGRES_DB:-postgres}" \
            --no-password \
            --no-psqlrc \
            --quiet \
            -v ON_ERROR_STOP=1 \
            -v reader_user="$reader_user" \
            -v reader_password="$reader_pw" \
            -v exporter_user="$exporter_user" \
            -v exporter_password="$exporter_pw" \
            -f "$f" || return 1
    done

    echo "bct-init: cluster initialisation complete."
}

bct_init
