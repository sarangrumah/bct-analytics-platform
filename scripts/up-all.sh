#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Bring EVERY product stack up, in the one order that works.
#
#     make up
#
# Before this existed, a full stack needed five commands in a specific order
# that was documented in a Makefile comment and nowhere enforced. Getting it
# wrong does not fail loudly: a semantic-api started before the gateway cannot
# fetch JWKS and returns 401 on every VALID login, which reads as a client-side
# auth failure and sends people to debug the wrong service.
#
# THE ORDER, and why each step is where it is:
#
#   1. odoo stack      postgres, redis, odoo. Everything else reads one of them.
#   2. warehouse       the landing zone must exist before a loader can fill it.
#   3. platform        login-gateway. BEFORE semantic-api, which verifies tokens
#                      against the gateway's published JWKS.
#   4. semantic-api    needs the warehouse (step 2) and the gateway (step 3).
#   5. insight-portal  needs semantic-api healthy to render a single figure.
#
# `cdc` is deliberately NOT started here. It needs a publication that only
# `make cdc-start` creates, and starting a consumer before its publication is a
# startup failure. WAL retention also begins the instant a slot exists and the
# 2 GB cap (ADR 0001) starts counting immediately, so a slot created before
# anyone is ready to consume it is the exact failure that cap exists to bound.
#
# Observability is also not started here — `make up-obs` is its own step, as
# before.
# ---------------------------------------------------------------------------
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

BUILD_FLAG="--build"
SKIP_INIT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --no-build)  BUILD_FLAG=""; shift ;;
        --skip-init) SKIP_INIT="--skip-init"; shift ;;
        -h|--help)
            printf 'usage: %s [--no-build] [--skip-init]\n' "$0" >&2; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

require_docker
[ -f "$ENV_FILE" ] || die ".env not found — run 'make dev-bootstrap' first."
load_env

BIND="${BIND_ADDRESS:-127.0.0.1}"

# --- 1. odoo ---------------------------------------------------------------
log "[1/5] odoo stack (postgres, redis, odoo)"
# up-dev.sh owns database initialisation and the dev password; re-implementing
# either here would give two places to fix the next time one of them changes.
# up-dev.sh builds by default and takes --no-build to opt out. That is the
# inverse of the --build flag compose wants, so translate rather than forward:
# passing "--build" straight through makes up-dev.sh die on an unknown argument.
if [ -n "$BUILD_FLAG" ]; then
    bash "$REPO_ROOT/scripts/up-dev.sh" ${SKIP_INIT}
else
    bash "$REPO_ROOT/scripts/up-dev.sh" --no-build ${SKIP_INIT}
fi

# --- 2. warehouse ----------------------------------------------------------
log "[2/5] insight stack: warehouse"
dc_insight up -d ${BUILD_FLAG} warehouse-db warehouse-exporter
WAIT_TIMEOUT=180 wait_healthy warehouse-db || die "warehouse-db did not become healthy."

# --- 3. platform -----------------------------------------------------------
log "[3/5] platform stack: login-gateway + caddy"
# Refuses to overwrite an existing key, so this is safe on every run and
# removes the one manual step between a fresh clone and a working gateway.
bash "$REPO_ROOT/scripts/analytics/gen-jwt-keys.sh"
dc_platform up -d ${BUILD_FLAG} login-gateway
WAIT_TIMEOUT=120 wait_healthy login-gateway || die "login-gateway did not become healthy."

# Caddy last in this step, and it is not optional decoration: dbfilter is ^%d$,
# so without a proxy setting Host per hostname the admin console is the only
# thing that breaks — but the moment a second client exists, nothing resolves.
dc_platform up -d caddy
WAIT_TIMEOUT=90 wait_healthy caddy || die "caddy did not become healthy."

# --- 4. semantic-api -------------------------------------------------------
log "[4/5] insight stack: semantic-api"
dc_insight up -d ${BUILD_FLAG} semantic-api
WAIT_TIMEOUT=120 wait_healthy semantic-api || die "semantic-api did not become healthy."

# --- 5. insight-portal -----------------------------------------------------
log "[5/5] insight stack: insight-portal"
dc_insight up -d ${BUILD_FLAG} insight-portal
WAIT_TIMEOUT=180 wait_healthy insight-portal || die "insight-portal did not become healthy."

# --- Report ----------------------------------------------------------------
# Probed, not assumed. A container reporting healthy and an endpoint answering
# from the host are different claims, and the host port binding is exactly what
# a healthcheck running inside the container cannot test.
# $4 is an optional Host header. Odoo's dbfilter is ^%d$ now that Caddy is the
# entry point, and %d is the FIRST LABEL of the host — so a request to
# 127.0.0.1 resolves %d to "127", matches no database, and answers 303 to a
# selector that list_db=False has disabled. Sending the tenant's hostname is
# not a workaround; it is how a request names the database it means.
probe() {
    local label="$1" url="$2" want="$3" host="${4:-}" code
    if [ -n "$host" ]; then
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 -H "Host: $host" "$url" || echo 000)"
    else
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$url" || echo 000)"
    fi
    if [ "$code" = "$want" ]; then
        printf '  %-16s %-46s %s\n' "$label" "${host:-$url}" "$code" >&2
    else
        printf '  %-16s %-46s %s  (expected %s)\n' "$label" "${host:-$url}" "$code" "$want" >&2
        return 1
    fi
}

log "endpoints"
rc=0
probe odoo           "http://$BIND:${ODOO_HOST_HTTP_PORT:-38069}/web/login"                  200 "${ODOO_DB_NAME:-bct}.athera.localhost" || rc=1
probe login-gateway  "http://$BIND:${LOGIN_GATEWAY_HOST_PORT:-38120}/.well-known/jwks.json"  200 || rc=1
probe semantic-api   "http://$BIND:${SEMANTIC_API_HOST_PORT:-38200}/healthz"                 200 || rc=1
probe insight-portal "http://$BIND:${INSIGHT_PORTAL_HOST_PORT:-33000}/healthz"               200 || rc=1
probe admin-console  "http://$BIND:${ODOO_HOST_HTTP_PORT:-38069}/web/login"                  200 "${ATHERA_ADMIN_DB:-athera_admin}.athera.localhost" || rc=1
probe caddy          "http://$BIND:${CADDY_HTTP_PORT:-38080}/"                               308 "athera.localhost" || rc=1

echo "" >&2
info "cdc is not started by this script — run 'make cdc-start' once the"
info "publication is wanted. 'make up-obs' starts the observability stack."

exit "$rc"
