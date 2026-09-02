#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Create the ATHERA control-plane database and apply its schema.
#
#     make control-plane
#
# Idempotent. Safe to run against a live cluster, which is the point: the
# schema file also sits in postgres/init/sql/ for a fresh cluster, but that
# directory only ever executes on FIRST BOOT. An existing pgdata volume — which
# is every developer machine that already ran this project — would never see
# it. A schema that only lands on machines nobody has is not a schema.
#
# WHAT IT BUILDS
#
#   1. ${ATHERA_ADMIN_DB} — an Odoo database holding the super-admin console.
#      This is the diagram's "Super Admin CMS" surface. It is a SECOND Odoo
#      database in the same cluster, served by the same Odoo container.
#   2. tenant_registry.* INSIDE that database. Not in a separate master DB:
#      custom_super_admin reads the audit view with a plain cr.execute, and
#      Postgres has no cross-database SELECT. See the header of
#      postgres/init/sql/40-tenant-registry.sql.
#   3. Two cluster-level roles: tenant_orchestrator (the sole writer) and
#      tenant_registry_reader (what Odoo reads through).
#   4. A registry row for the existing `bct` tenant, so the control plane
#      describes reality rather than starting empty next to a live client.
#
# THE DBFILTER CONSEQUENCE, stated because it is a real widening.
# Odoo serves a database only if ODOO_DBFILTER matches it. Adding an admin
# database means the filter must name it. This script does NOT edit .env; it
# checks and tells you, because contract 04 s8 makes widening that filter a
# reviewed edit rather than a side effect of running a script. The widened form
# is still fully anchored — ^(bct|athera_admin)$ — and admits exactly two
# names, which is a different thing from the per-host ^%d$ form that Caddy will
# bring later and that would admit every database in the cluster.
# ---------------------------------------------------------------------------
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

SEED_BCT=1
while [ $# -gt 0 ]; do
    case "$1" in
        --no-seed) SEED_BCT=0; shift ;;
        -h|--help) printf 'usage: %s [--no-seed]\n' "$0" >&2; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

require_docker
load_env
require_healthy postgres

ADMIN_DB="${ATHERA_ADMIN_DB:-athera_admin}"
ADMIN_MODULES="${ATHERA_ADMIN_MODULES:-custom_core,custom_super_admin,custom_hub_console,custom_onboarding_journey,custom_tenant_infra}"
ORCH_USER="${ORCHESTRATOR_DB_USER:-tenant_orchestrator}"
ORCH_PASS="${ORCHESTRATOR_DB_PASSWORD:?ORCHESTRATOR_DB_PASSWORD is required — run 'make dev-bootstrap'}"
GW_USER="${LOGIN_GATEWAY_REGISTRY_USER:-login_gateway_registry}"
GW_PASS="${LOGIN_GATEWAY_REGISTRY_PASSWORD:?LOGIN_GATEWAY_REGISTRY_PASSWORD is required — run 'make dev-bootstrap'}"
SITE_USER="${MARKETING_SITE_DB_USER:-marketing_site_reader}"
SITE_PASS="${MARKETING_SITE_DB_PASSWORD:?MARKETING_SITE_DB_PASSWORD is required - run 'make dev-bootstrap'}"

validate_slug "$ADMIN_DB"

# --- 1. the admin Odoo database -------------------------------------------
if db_initialised "$ADMIN_DB"; then
    log "[1/4] '$ADMIN_DB' already carries an Odoo schema"
else
    log "[1/4] creating '$ADMIN_DB' with: $ADMIN_MODULES"
    # Odoo creates the database itself so it gets the encoding and collation it
    # requires from template0; createdb by hand risks a collation the registry
    # loader then rejects.
    odoo_was_running=0
    if [ "$(docker inspect -f '{{.State.Running}}' "$(container_of odoo)" 2>/dev/null)" = "true" ]; then
        odoo_was_running=1
        # Same deadlock as init-db.sh: module DDL against tables a live worker
        # pool holds open blocks on itself.
        log "stopping odoo: DDL against a database its workers hold will deadlock"
        dc stop odoo >/dev/null
    fi
    set +e
    dc run --rm --no-deps -T odoo \
        odoo -d "$ADMIN_DB" -i "$ADMIN_MODULES" \
             --stop-after-init --without-demo=True --load-language=en_US
    rc=$?
    set -e
    if [ "$odoo_was_running" -eq 1 ]; then
        dc up -d odoo >/dev/null
        wait_healthy odoo || warn "odoo did not come back healthy — check 'make logs'."
    fi
    [ "$rc" -eq 0 ] || die "odoo exited $rc creating '$ADMIN_DB'. See the traceback above."
    db_initialised "$ADMIN_DB" || die "odoo exited 0 but '$ADMIN_DB' has no ir_module_module."
fi

# --- 2. roles, before the schema that grants to them ----------------------
# Cluster-level, so they are created from the maintenance database. Created
# before the schema because the GRANT statements in step 3 name them; the
# reverse order fails on a fresh cluster with "role does not exist".
log "[2/4] control-plane roles"
psql_super "$POSTGRES_DB" -q -v ON_ERROR_STOP=1 \
    -v orch="$ORCH_USER" -v orchpass="$ORCH_PASS" \
    -v gw="$GW_USER" -v gwpass="$GW_PASS" \
    -v site="$SITE_USER" -v sitepass="$SITE_PASS" <<'SQL'
-- \gexec, not a DO block. psql does NOT substitute :'vars' inside a
-- dollar-quoted body, so EXECUTE format(..., :'orch') inside DO $do$ ... $do$
-- reaches the server as the literal text :'orch' and fails at runtime.
-- Building the statement in a SELECT and running it through \gexec keeps the
-- credential out of the shell's argv AND out of a dollar-quoted string.
--
-- \o /dev/null is NOT cosmetic. \gexec prints the statement it is about to
-- run, and that statement contains the role's password in clear text — so the
-- first version of this script wrote the orchestrator's credential into the
-- terminal and into any CI log capturing it. Redirecting query output still
-- lets \gexec execute the result; it only stops psql echoing it.
\o /dev/null
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'orch', :'orchpass')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'orch');
\gexec
SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :'orch', :'orchpass')
 WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'orch');
\gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'site', :'sitepass')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'site');
\gexec
SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :'site', :'sitepass')
 WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'site');
\gexec
SELECT 'CREATE ROLE tenant_registry_reader NOLOGIN'
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tenant_registry_reader');
\gexec
-- The login-gateway's own identity. tenant_registry_reader is NOLOGIN, so the
-- gateway cannot connect as it directly; this role can log in and inherits
-- exactly that reader and nothing more. It holds no grant on any tenant
-- database, so "the gateway cannot read client data" is a property of the
-- role, not of the gateway's code.
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'gw', :'gwpass')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'gw');
\gexec
SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :'gw', :'gwpass')
 WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'gw');
\gexec
\o
SQL

# CREATEDB is what lets the orchestrator provision a tenant database at all.
# It is the one cluster-level privilege it holds; it is NOT a superuser, so it
# cannot bypass RLS anywhere and cannot read another role's tables.
psql_super "$POSTGRES_DB" -q -c "ALTER ROLE \"$ORCH_USER\" CREATEDB;"

# --- 3. the schema --------------------------------------------------------
log "[3/4] applying tenant_registry to '$ADMIN_DB'"
dc exec -T postgres psql -v ON_ERROR_STOP=1 --no-psqlrc --quiet \
    -U "$POSTGRES_USER" -d "$ADMIN_DB" \
    < "$REPO_ROOT/postgres/init/sql/40-tenant-registry.sql"

# The CMS lives in the SAME database. The diagram draws one Postgres under the
# left branch, and both halves are edited by the same people from the same
# console; a second cluster would buy nothing and cost a join.
log "      + cms schema (the public site content)"
dc exec -T postgres psql -v ON_ERROR_STOP=1 --no-psqlrc --quiet \
    -U "$POSTGRES_USER" -d "$ADMIN_DB" \
    < "$REPO_ROOT/postgres/init/sql/41-cms.sql"

# Grants live here rather than in the schema file because the schema file also
# runs from docker-entrypoint-initdb.d on a fresh cluster, where the Odoo role
# that needs the grant does not exist yet.
psql_super "$ADMIN_DB" -q -v ON_ERROR_STOP=1 <<SQL
GRANT USAGE ON SCHEMA tenant_registry TO "$ORCH_USER", tenant_registry_reader;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA tenant_registry TO "$ORCH_USER";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA tenant_registry TO "$ORCH_USER";
GRANT SELECT ON ALL TABLES IN SCHEMA tenant_registry TO tenant_registry_reader;
GRANT EXECUTE ON FUNCTION tenant_registry.is_active(TEXT) TO "$ORCH_USER", tenant_registry_reader;
GRANT EXECUTE ON FUNCTION tenant_registry.entitlements(TEXT) TO "$ORCH_USER", tenant_registry_reader;
GRANT EXECUTE ON FUNCTION tenant_registry.verify_action_chain(INTEGER) TO "$ORCH_USER", tenant_registry_reader;
-- Future tables in this schema, so a migration does not silently leave the
-- reader blind to whatever it adds.
ALTER DEFAULT PRIVILEGES IN SCHEMA tenant_registry
    GRANT SELECT ON TABLES TO tenant_registry_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA tenant_registry
    GRANT SELECT, INSERT, UPDATE ON TABLES TO "$ORCH_USER";
-- custom_super_admin reads through the Odoo role, so that role inherits the
-- reader. This is the grant the module's MODULE_KNOWLEDGE.md calls a hard
-- requirement.
GRANT tenant_registry_reader TO "$POSTGRES_USER";
GRANT tenant_registry_reader TO "$GW_USER";
GRANT CONNECT ON DATABASE "$ADMIN_DB" TO "$GW_USER";
-- The public site reads published content and nothing else: SELECT on the two
-- views only, NOT on cms.page. A draft is therefore not merely filtered out of
-- its queries -- it is outside what the role can reach at all.
GRANT USAGE ON SCHEMA cms TO "$SITE_USER";
GRANT SELECT ON cms.published_page, cms.published_block TO "$SITE_USER";
GRANT CONNECT ON DATABASE "$ADMIN_DB" TO "$SITE_USER";
-- The console edits through the orchestrator, so that role owns the writes.
GRANT USAGE ON SCHEMA cms TO "$ORCH_USER";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA cms TO "$ORCH_USER";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA cms TO "$ORCH_USER";
SQL

# --- 4. describe reality --------------------------------------------------
if [ "$SEED_BCT" -eq 1 ]; then
    log "[4/4] registering the existing '$ODOO_DB_NAME' tenant"
    # A control plane that starts empty beside a live client is a control plane
    # that is already wrong. `suite` because bct is the internal tenant and has
    # every product; valid_until NULL means open-ended, which is what
    # is_active() treats as a manually managed account.
    psql_super "$ADMIN_DB" -q -v ON_ERROR_STOP=1 <<SQL
INSERT INTO tenant_registry.tenants (slug, display_name, db_name, state, plan_code, activated_at)
VALUES ('$ODOO_DB_NAME', 'BCT (internal)', '$ODOO_DB_NAME', 'active', 'suite', now())
ON CONFLICT (slug) DO NOTHING;
SQL
else
    log "[4/4] skipping tenant seed (--no-seed)"
fi

# --- report ---------------------------------------------------------------
psql_super "$ADMIN_DB" -tAc \
    "SELECT '  tenants: ' || count(*) FROM tenant_registry.tenants" | grep . >&2 || true
psql_super "$ADMIN_DB" -tAc \
    "SELECT '  plans:   ' || count(*) FROM tenant_registry.plans" | grep . >&2 || true
psql_super "$ADMIN_DB" -tAc \
    "SELECT '  is_active(''$ODOO_DB_NAME'') = ' || tenant_registry.is_active('$ODOO_DB_NAME')" | grep . >&2 || true

# The dbfilter check. Reported, never edited: see the header.
case "${ODOO_DBFILTER:-}" in
    *'%d'*)
        # The per-host form. It admits whatever the Host header's first label
        # names, so the admin console is reachable as long as caddy/Caddyfile
        # rewrites its route's Host (and X-Forwarded-Host) to
        # <admin-db>.<domain> and compose/odoo.yml carries the matching network
        # alias. Both are in place; this is the intended end state, not a gap.
        log "ODOO_DBFILTER is per-host (^%d\$) — '$ADMIN_DB' is reached via its hostname"
        ;;
    *"$ADMIN_DB"*) log "ODOO_DBFILTER names '$ADMIN_DB' explicitly" ;;
    *)
        warn "ODOO_DBFILTER is '${ODOO_DBFILTER:-unset}' and does NOT admit '$ADMIN_DB'."
        warn "  Odoo will refuse to serve the admin console until it does. Edit .env:"
        warn "      ODOO_DBFILTER=^(${ODOO_DB_NAME}|${ADMIN_DB})\$"
        warn "  then:  make restart SERVICE=odoo"
        ;;
esac
