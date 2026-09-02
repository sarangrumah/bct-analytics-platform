#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Shared helpers for scripts/*.sh. Source it, do not execute it:
#
#     . "$(dirname "$0")/lib/common.sh"
#
# Everything here is deliberately dependency-free. `jq` is NOT installed on the
# target host, so any JSON handling goes through python3.
# ---------------------------------------------------------------------------

# -E so ERR traps survive into functions; -u so a typo'd variable is an error
# rather than an empty string that silently drops a psql flag.
set -Eeuo pipefail

# ---------------------------------------------------------------------------
# Git Bash / MSYS path mangling.
#
# Under MSYS, a POSIX-looking argument to a native Windows .exe is rewritten to
# a Windows path. That is right for host paths and WRONG for container paths:
# `docker exec ... /var/lib/odoo` arrives inside the container as
# `C:/Program Files/Git/var/lib/odoo`.
#
# MSYS_NO_PATHCONV must therefore be scoped to the docker invocation and NOT
# exported globally. Exporting it breaks every other native tool: the host's
# python3.exe then receives `/e/Projects/...` literally and resolves it as
# `E:\e\Projects\...`, which does not exist. That failure is why this is a
# function and not an `export`.
#
# It is a no-op on Linux and macOS.
# ---------------------------------------------------------------------------
IS_MSYS=0
case "$(uname -s 2>/dev/null || echo unknown)" in
    MINGW*|MSYS*|CYGWIN*) IS_MSYS=1 ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export REPO_ROOT
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
ENV_EXAMPLE="$REPO_ROOT/.env.example"

# --- Output ----------------------------------------------------------------
if [ -t 2 ] && [ -z "${NO_COLOR:-}" ]; then
    _C_RED=$'\033[31m'; _C_YEL=$'\033[33m'; _C_GRN=$'\033[32m'
    _C_DIM=$'\033[2m';  _C_OFF=$'\033[0m'
else
    _C_RED=''; _C_YEL=''; _C_GRN=''; _C_DIM=''; _C_OFF=''
fi

log()   { printf '%s==>%s %s\n' "$_C_GRN" "$_C_OFF" "$*" >&2; }
info()  { printf '%s    %s%s\n' "$_C_DIM" "$*" "$_C_OFF" >&2; }
warn()  { printf '%swarn:%s %s\n' "$_C_YEL" "$_C_OFF" "$*" >&2; }
die()   { printf '%serror:%s %s\n' "$_C_RED" "$_C_OFF" "$*" >&2; exit 1; }

# --- Environment -----------------------------------------------------------
# Parsed line by line rather than `source`d. Sourcing an untrusted .env runs
# arbitrary shell, and it also mangles values containing characters the shell
# treats specially (ODOO_DBFILTER=^bct$ is exactly such a value).
load_env() {
    [ -f "$ENV_FILE" ] || die ".env not found at $ENV_FILE — run 'make dev-bootstrap' first."
    local line key val
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"                       # tolerate a CRLF .env
        case "$line" in ''|'#'*) continue ;; esac
        case "$line" in *=*) ;; *) continue ;; esac
        key="${line%%=*}"
        val="${line#*=}"
        [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        # Strip one layer of surrounding quotes if present.
        case "$val" in
            \"*\") val="${val#\"}"; val="${val%\"}" ;;
            \'*\') val="${val#\'}"; val="${val%\'}" ;;
        esac
        export "$key=$val"
    done < "$ENV_FILE"

    : "${COMPOSE_PROJECT_NAME:=odoo19-bct}"
    : "${POSTGRES_USER:=odoo}"
    : "${POSTGRES_DB:=postgres}"
    : "${ODOO_DB_NAME:=bct}"
    : "${WAREHOUSE_READER_USER:=warehouse_reader}"
    : "${BACKUP_DIR:=./backups}"
    : "${BACKUP_RETENTION_DAYS:=14}"
    export COMPOSE_PROJECT_NAME POSTGRES_USER POSTGRES_DB ODOO_DB_NAME \
           WAREHOUSE_READER_USER BACKUP_DIR BACKUP_RETENTION_DAYS
}

# --- Compose ---------------------------------------------------------------
# EVERY compose call goes through here.
#
# This host runs odoo19-platform-*, odoo19-analytics-* and smart-warga-*
# alongside this project. An unscoped `docker compose down` would take out
# whichever project the current directory happened to resolve to. -p is not
# optional, and -f is explicit so the scripts do not depend on COMPOSE_FILE
# being set in the caller's environment.
#
# The -f paths are RELATIVE and the call runs from $REPO_ROOT in a subshell.
# That is required, not stylistic: MSYS path conversion is disabled for this
# command so container-side paths survive, which would also leave an absolute
# host-side `-f /e/Projects/...` unconverted and unopenable by docker.exe.
# A relative path needs no conversion, so both work at once.
#
# --env-file is passed explicitly. The compose files moved to compose/ on
# 2026-09-01, and compose looks for `.env` in the PROJECT DIRECTORY, which is
# the directory of the first -f file. Without this flag every `${VAR}` would
# resolve empty. It is belt-and-braces here because load_env has already
# exported the same values into this process, and a real environment variable
# wins over --env-file either way — but a script that forgets load_env should
# still get a working stack rather than a confusing interpolation error.
#
# COMPOSE_FILES_ODOO is the odoo product stack. Callers that need another
# stack pass its file through dc_with, below.
COMPOSE_FILES_ODOO=(-f compose/odoo.yml -f compose/odoo.dev.yml)

# dc_with FILE... -- ARGS...   run compose with an explicit file list
dc_with() {
    local files=()
    while [ $# -gt 0 ] && [ "$1" != "--" ]; do files+=("$1"); shift; done
    [ "${1:-}" = "--" ] && shift
    (
        cd "$REPO_ROOT"
        # A call that names only some of the project's files sees the rest of
        # the project's containers as orphans and prints a scary warning on
        # every `up`. It never removes them — that needs --remove-orphans,
        # which nothing here passes — but the warning trains people to ignore
        # compose output.
        export COMPOSE_IGNORE_ORPHANS=true
        if [ "$IS_MSYS" = "1" ]; then
            MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
                docker compose -p "$COMPOSE_PROJECT_NAME" --env-file .env \
                    "${files[@]}" "$@"
        else
            docker compose -p "$COMPOSE_PROJECT_NAME" --env-file .env \
                "${files[@]}" "$@"
        fi
    )
}

# The odoo product stack — postgres, redis, odoo. The default for every script
# that predates the product split.
dc() { dc_with "${COMPOSE_FILES_ODOO[@]}" -- "$@"; }

# The insight product stack — warehouse-db, cdc, semantic-api, insight-portal.
dc_insight() { dc_with "${COMPOSE_FILES_ODOO[@]}" -f compose/insight.yml -- "$@"; }

# The shared platform stack — login-gateway, and later the orchestrator, the
# super-admin console, caddy and the marketing site.
dc_platform() { dc_with "${COMPOSE_FILES_ODOO[@]}" -f compose/platform.yml -- "$@"; }

# --- Guards ----------------------------------------------------------------
require_docker() {
    command -v docker >/dev/null 2>&1 || die "docker not found on PATH."
    docker info >/dev/null 2>&1 || die "docker daemon is not reachable."
}

container_of() { printf '%s-%s' "$COMPOSE_PROJECT_NAME" "$1"; }

health_of() {
    docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
        "$(container_of "$1")" 2>/dev/null || echo "absent"
}

require_healthy() {
    local svc status
    for svc in "$@"; do
        status="$(health_of "$svc")"
        [ "$status" = "healthy" ] || \
            die "service '$svc' is '$status', expected 'healthy'. Run 'make up-dev' first."
    done
}

# Waits for services to report healthy. Returns non-zero on timeout and prints
# the last log lines of whatever did not come up — a bare timeout message with
# no logs is the most common way to waste an hour.
wait_healthy() {
    local timeout="${WAIT_TIMEOUT:-300}"
    local deadline=$(( SECONDS + timeout ))
    local svc status pending
    while :; do
        pending=""
        for svc in "$@"; do
            status="$(health_of "$svc")"
            [ "$status" = "healthy" ] || pending="$pending $svc($status)"
        done
        [ -z "$pending" ] && { log "healthy:$(printf ' %s' "$@")"; return 0; }
        if [ "$SECONDS" -ge "$deadline" ]; then
            warn "timed out after ${timeout}s waiting for:$pending"
            for svc in "$@"; do
                [ "$(health_of "$svc")" = "healthy" ] && continue
                printf '\n--- last 40 log lines: %s ---\n' "$svc" >&2
                # `docker logs` on the container name, not `dc logs` on the
                # service: this helper is now called for services that live in
                # the insight and platform stacks too, and `dc` names only the
                # odoo files, so `dc logs semantic-api` would print nothing at
                # exactly the moment the logs matter most.
                docker logs --tail 40 "$(container_of "$svc")" >&2 2>&1 || true
            done
            return 1
        fi
        info "waiting for:$pending"
        sleep 3
    done
}

# --- Postgres --------------------------------------------------------------
# -T disables TTY allocation, which is required for every non-interactive call
# (and mandatory on Windows, where a TTY request fails outright).
psql_super() {
    local db="$1"; shift
    dc exec -T postgres psql -v ON_ERROR_STOP=1 --no-psqlrc \
        -U "$POSTGRES_USER" -d "$db" "$@"
}

db_exists() {
    local out
    out="$(psql_super "$POSTGRES_DB" -tAc \
        "SELECT 1 FROM pg_database WHERE datname = '$1'" 2>/dev/null || true)"
    [ "$out" = "1" ]
}

# "Initialised" means Odoo has actually built its schema, not merely that a
# database exists. A half-created database with no ir_module_module is the
# state you get when an install is interrupted, and treating it as done
# produces a 500 on /web/login with no obvious cause.
db_initialised() {
    local out
    db_exists "$1" || return 1
    out="$(psql_super "$1" -tAc "SELECT to_regclass('public.ir_module_module') IS NOT NULL" 2>/dev/null || true)"
    [ "$out" = "t" ]
}

# --- Naming ----------------------------------------------------------------
# Tenant slugs become database names, psql identifiers, replication slot names
# and file paths. Postgres slot names allow only [a-z0-9_], which is the
# tightest of those, so that is the rule everywhere.
validate_slug() {
    local slug="$1"
    [[ "$slug" =~ ^[a-z][a-z0-9_]{1,30}$ ]] || die \
        "invalid tenant slug '$slug'. Must match ^[a-z][a-z0-9_]{1,30}\$ — lowercase, starts with a letter, no dashes (Postgres replication slot names forbid them)."
    case "$slug" in
        postgres|template0|template1|odoo)
            die "tenant slug '$slug' is reserved." ;;
    esac
}

confirm() {
    local prompt="$1"
    if [ "${ASSUME_YES:-0}" = "1" ]; then
        info "--yes given: $prompt"
        return 0
    fi
    [ -t 0 ] || die "$prompt (refusing in a non-interactive shell; pass --yes to proceed)"
    printf '%s [y/N] ' "$prompt" >&2
    local reply; read -r reply
    case "$reply" in y|Y|yes|YES) return 0 ;; *) die "aborted." ;; esac
}
