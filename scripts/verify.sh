#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Run every Phase 1 acceptance check and print the evidence, verbatim.
#
#     make verify
#
# This exists so the Lead's review (PLAN.md, "Lead review duty": no claim is
# accepted on assertion) is a single command that either exits 0 or shows
# exactly which criterion failed. It re-runs the commands from the brief's
# "Evidence required" block, in order, and adds the checks that block does not
# cover.
# ---------------------------------------------------------------------------
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

require_docker
load_env

PASS=0
FAIL=0
SKIPPED=0
RESULTS=()

step() { printf '\n\033[1m=== %s ===\033[0m\n' "$*"; }

check() {
    local label="$1"; shift
    if "$@"; then
        RESULTS+=("PASS  $label")
        PASS=$((PASS + 1))
    else
        RESULTS+=("FAIL  $label")
        FAIL=$((FAIL + 1))
    fi
}

# A check that cannot apply on this host is NOT a pass and NOT a failure.
# Recording it as either is worse than recording it as neither: a false FAIL
# trains people to read a red summary as normal, and a false PASS claims
# evidence that was never gathered.
skip() {
    RESULTS+=("SKIP  $1  ($2)")
    SKIPPED=$((SKIPPED + 1))
}

# 1 -------------------------------------------------------------------------
step "1. compose config validates"
check "compose config -q exits 0" \
    bash -c 'docker compose --env-file .env -f compose/odoo.yml -f compose/odoo.dev.yml config -q && echo CONFIG_OK'

# 2 -------------------------------------------------------------------------
step "2. services healthy"
dc ps
check "postgres healthy" bash -c "[ \"\$(docker inspect -f '{{.State.Health.Status}}' ${COMPOSE_PROJECT_NAME}-postgres)\" = healthy ]"
check "redis healthy"    bash -c "[ \"\$(docker inspect -f '{{.State.Health.Status}}' ${COMPOSE_PROJECT_NAME}-redis)\"    = healthy ]"
check "odoo healthy"     bash -c "[ \"\$(docker inspect -f '{{.State.Health.Status}}' ${COMPOSE_PROJECT_NAME}-odoo)\"     = healthy ]"

# 3 and 4 -------------------------------------------------------------------
step "3+4. postgres logical decoding settings"
dc exec -T postgres psql -U odoo -tAc \
    "show wal_level; show max_replication_slots; show max_wal_senders; show max_slot_wal_keep_size;"
check "wal_level = logical" bash -c \
    "[ \"\$(docker compose -p $COMPOSE_PROJECT_NAME --env-file .env -f compose/odoo.yml -f compose/odoo.dev.yml exec -T postgres psql -U odoo -tAc 'show wal_level')\" = logical ]"
check "max_slot_wal_keep_size is bounded (not -1)" bash -c \
    "v=\$(docker compose -p $COMPOSE_PROJECT_NAME --env-file .env -f compose/odoo.yml -f compose/odoo.dev.yml exec -T postgres psql -U odoo -tAc 'show max_slot_wal_keep_size'); echo \"  value=\$v\"; [ \"\$v\" != '-1' ] && [ -n \"\$v\" ]"

# 5 -------------------------------------------------------------------------
step "3. /web/login returns 200"
URL="http://${BIND_ADDRESS:-127.0.0.1}:${ODOO_HOST_HTTP_PORT:-38069}/web/login"
curl -s -o /dev/null -w "login=%{http_code}\n" "$URL"
# The Host header names the database. dbfilter is ^%d$ now that Caddy is the
# entry point, and %d is the first label of the host: without this the request
# resolves %d to "127", matches nothing, and answers 303 to a database selector
# that list_db=False has disabled. Checking the bare URL would report a healthy
# stack as broken.
VHOST="${ODOO_DB_NAME:-bct}.athera.localhost"
check "/web/login = 200 (Host: $VHOST)" bash -c "[ \"\$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: $VHOST' '$URL')\" = 200 ]"
check "admin console = 200" bash -c "[ \"\$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: ${ATHERA_ADMIN_DB:-athera_admin}.athera.localhost' '$URL')\" = 200 ]"

# 6 -------------------------------------------------------------------------
step "4. odoo runs as a non-root uid"
dc exec -T odoo id
check "odoo uid != 0" bash -c \
    "[ \"\$(docker compose -p $COMPOSE_PROJECT_NAME --env-file .env -f compose/odoo.yml -f compose/odoo.dev.yml exec -T odoo id -u | tr -d '\r')\" != 0 ]"

# 7 -------------------------------------------------------------------------
step "5. no setuid/setgid binaries in the odoo image"
suid="$(dc exec -T odoo find / -xdev -perm /6000 -type f 2>/dev/null | tr -d '\r' || true)"
if [ -n "$suid" ]; then printf '%s\n' "$suid"; else echo "(none)"; fi
check "no SUID/SGID files" bash -c "[ -z \"$suid\" ]"

# 8 -------------------------------------------------------------------------
step "6. warehouse_reader is read-only by construction"
check "warehouse-reader-check.sh" bash "$REPO_ROOT/scripts/warehouse-reader-check.sh"

# 9 -------------------------------------------------------------------------
step "7. no real secret in tracked files"
check "scan-secrets" python3 "$REPO_ROOT/scripts/scan-secrets.py"

# 10 ------------------------------------------------------------------------
step "8. make help documents every target"
# `make help` colourises target names, so the raw output starts each line with
# an ANSI escape, not the target. Strip escapes before comparing, or every
# single target reads as undocumented.
help_targets="$(make -s -C "$REPO_ROOT" help 2>/dev/null \
                | sed 's/\x1b\[[0-9;]*m//g' \
                | grep -E '^ {4}[a-zA-Z0-9_-]+ ' \
                | awk '{print $1}' | sort -u)"
phony_targets="$(grep -Eo '^\.PHONY: [a-zA-Z0-9_-]+' "$REPO_ROOT/Makefile" \
                | awk '{print $2}' | sort -u)"
undocumented="$(comm -23 <(printf '%s\n' "$phony_targets") <(printf '%s\n' "$help_targets"))"
if [ -n "$undocumented" ]; then echo "undocumented targets:"; printf '  %s\n' $undocumented; else echo "every .PHONY target appears in 'make help'"; fi
check "no undocumented targets" bash -c "[ -z \"$undocumented\" ]"

# 11 ------------------------------------------------------------------------
step "9. other stacks on this host are untouched"
# These two siblings live on the development workstation, not on a deployment
# host. Asserting them unconditionally makes `make verify` report two permanent
# FAILs on any machine that legitimately runs only this stack - and a summary
# that is always red is a summary nobody reads, which is how a real failure
# gets past a reviewer. The check keeps its teeth where it has any: a sibling
# that EXISTS must still be Up. Only one that was never there is skipped.
# `|| true` because common.sh sets -Eeuo pipefail: with no sibling present grep
# exits 1, pipefail raises it, and set -e kills the script BEFORE the loop below
# ever runs. This is a reporting line, not an assertion - the assertions are in
# the loop. Found by running verify on the deployment host this block was
# rewritten for, which is where it should have been proven in the first place.
docker ps -a --format '{{.Names}}\t{{.Status}}' | grep -E 'odoo19-(platform|analytics)' | head || true
for sibling in odoo19-platform-odoo odoo19-analytics-odoo; do
    if docker ps -a --format '{{.Names}}' | grep -qx "$sibling"; then
        check "$sibling still up" bash -c \
            "docker ps --format '{{.Names}}\t{{.Status}}' | grep -q '$sibling.*Up'"
    else
        skip "$sibling still up" "not present on this host"
    fi
done

# 12 ------------------------------------------------------------------------
step "10. .gitignore does not silently drop a file that must ship"
check "gitignore guard" python3 "$REPO_ROOT/scripts/check-gitignore.py"

# 13 ------------------------------------------------------------------------
step "11. every custom model can actually be searched"
# A model with no search view gives the user no filters, no Group By, and a
# search box that only looks at `name`. 285 of the imported models shipped that
# way. This gate lives here rather than in CI because it reads ir_model_fields
# from the running database: the question "does this model have a search view"
# has no honest answer from static files alone, since a view can come from any
# module in the graph.
check "search views complete" python3 "$REPO_ROOT/scripts/generate-search-views.py" --check

# 14 ------------------------------------------------------------------------
step "12. the alerting path is armed, not merely syntactically valid"
check "alerting armed" python3 "$REPO_ROOT/scripts/check-alerting.py"

# 14 ------------------------------------------------------------------------
step "13. the dev login credential is applied, and Odoo's default is refused"
# PLAN.md instance 10. The half of this that matters is the NEGATIVE: a check
# that only asserts "$BCT_DEV_USER_PASSWORD logs in" is green on a stack that
# accepts BOTH passwords, which is precisely the defective state. So
# --check requires authenticate('bct','admin','admin') to be False.
check "dev password applied, default rejected" \
    bash "$REPO_ROOT/scripts/set-dev-passwords.sh" --check

# base-stack footprint ------------------------------------------------------
step "base stack memory (constraint: idle under 4 GiB)"
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}' \
    "${COMPOSE_PROJECT_NAME}-postgres" "${COMPOSE_PROJECT_NAME}-redis" "${COMPOSE_PROJECT_NAME}-odoo"
total_mib="$(docker stats --no-stream --format '{{.MemUsage}}' \
    "${COMPOSE_PROJECT_NAME}-postgres" "${COMPOSE_PROJECT_NAME}-redis" "${COMPOSE_PROJECT_NAME}-odoo" \
    | awk '{print $1}' | python3 -c '
import sys
total = 0.0
for line in sys.stdin:
    v = line.strip()
    if not v: continue
    n = float("".join(c for c in v if c.isdigit() or c == "."))
    if v.upper().endswith("GIB"): n *= 1024
    elif v.upper().endswith("KIB"): n /= 1024
    elif v.upper().endswith("B") and not v.upper().endswith(("MIB","GIB","KIB")): n /= 1024*1024
    total += n
print(f"{total:.1f}")')"
echo "  base stack total: ${total_mib} MiB (limit 4096 MiB)"
check "base stack idles under 4 GiB" bash -c "python3 -c \"import sys; sys.exit(0 if $total_mib < 4096 else 1)\""

# --- summary ---------------------------------------------------------------
printf '\n\033[1m=== summary ===\033[0m\n'
printf '%s\n' "${RESULTS[@]}"
printf '\n%d passed, %d failed, %d skipped\n' "$PASS" "$FAIL" "$SKIPPED"
[ "$FAIL" -eq 0 ]
