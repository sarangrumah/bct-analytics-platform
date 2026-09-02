#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Bring the dev stack up and leave it actually usable.
#
#     make up-dev
#
# "Usable" means /web/login returns 200, not merely that three containers are
# running. That requires the database to be initialised, and initialisation has
# to happen while Postgres is up but before the long-running Odoo server is
# expected to be healthy — so the order below is not incidental:
#
#   1. postgres + redis        (odoo depends_on both being healthy)
#   2. init-db                 one-off `docker compose run`, idempotent
#   3. odoo                    now its healthcheck can hit a real database
#   4. wait                    with a real timeout and real logs on failure
#   5. set-dev-passwords       needs a RUNNING odoo, so it can only be last
#
# Doing 2 after 3 would leave Odoo unhealthy for its whole start_period on a
# clean machine, and would make `make up-dev` non-deterministic.
#
# Step 5 exists because of PLAN.md defect-pattern instance 10: the dev login
# password lived only in an untracked .env and nothing ever applied it, so the
# documented bring-up left `admin` on Odoo's default password while the file
# advertised a strong one. It is here, in the documented bring-up, so a fresh
# clone gets it without anyone remembering to. It is idempotent and it never
# fails the bring-up.
# ---------------------------------------------------------------------------
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

BUILD=1
SKIP_INIT=0
while [ $# -gt 0 ]; do
    case "$1" in
        --no-build)  BUILD=0; shift ;;
        --skip-init) SKIP_INIT=1; shift ;;
        -h|--help)
            printf 'usage: %s [--no-build] [--skip-init]\n' "$0" >&2; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

require_docker
[ -f "$ENV_FILE" ] || die ".env not found — run 'make dev-bootstrap' first."
load_env

[ -d "$REPO_ROOT/addons" ] || mkdir -p "$REPO_ROOT/addons"

if [ "$BUILD" -eq 1 ]; then
    log "[1/5] building the odoo image"
    dc build odoo
fi

log "[2/5] starting postgres and redis"
dc up -d postgres redis
WAIT_TIMEOUT=180 wait_healthy postgres redis || die "postgres/redis did not become healthy."

if [ "$SKIP_INIT" -eq 0 ]; then
    log "[3/5] ensuring the database is initialised"
    "$REPO_ROOT/scripts/init-db.sh"
else
    log "[3/5] skipping database init (--skip-init)"
fi

log "[4/5] starting odoo"
dc up -d
WAIT_TIMEOUT=180 wait_healthy postgres redis odoo || die "odoo did not become healthy."

URL="http://${BIND_ADDRESS:-127.0.0.1}:${ODOO_HOST_HTTP_PORT:-38069}/web/login"
# The Host header names the database. dbfilter is ^%d$ now that Caddy is the
# entry point, and %d is the FIRST LABEL of the host - so a bare request to
# 127.0.0.1 resolves %d to "127", matches no database, and answers 303 to a
# selector that list_db=False has disabled. Probing without it reports a
# HEALTHY stack as broken and exits 1, which stops step 1 of every bring-up on
# a fresh host. up-all.sh and verify.sh were corrected for this on 2026-09-01;
# this script was missed.
VHOST="${ODOO_DB_NAME:-bct}.${ATHERA_DOMAIN:-athera.localhost}"
code="$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $VHOST" "$URL" || echo 000)"
if [ "$code" = "200" ]; then
    log "stack is up. $URL (Host: $VHOST) -> HTTP $code"
else
    warn "$URL (Host: $VHOST) returned HTTP $code (expected 200). Recent odoo logs:"
    dc logs --tail 40 odoo >&2 || true
    exit 1
fi

# ---------------------------------------------------------------------------
# [5/5] The dev login credential.
#
# Deliberately NOT fatal to the bring-up. It is a convenience credential; a
# stack that is up and serving 200 is up even if this could not run. But it is
# also never silent - the script warns loudly on every state it cannot fix, and
# `make check-dev-passwords` (and `make verify`) turn the same question into a
# hard pass/fail with the negative included.
#
# The demo.*@contoh.invalid accounts do not exist yet on a fresh clone:
# custom_demo_seed generates nothing at install time and `make seed-demo` is a
# separate, explicit step. The script reports them absent and moves on; running
# it again after seeding picks them up. `make seed-demo` also chains it, so the
# accounts it creates are usable the moment it returns.
# ---------------------------------------------------------------------------
log "[5/5] applying the dev login password"
if ! "$REPO_ROOT/scripts/set-dev-passwords.sh"; then
    warn "set-dev-passwords.sh failed. The stack is up, but a login may still be"
    warn "  on an Odoo default. Re-run:  make set-dev-passwords"
fi

dc ps
