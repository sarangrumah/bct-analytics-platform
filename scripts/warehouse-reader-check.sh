#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Prove that `warehouse_reader` is read-only BY CONSTRUCTION.
#
#     scripts/warehouse-reader-check.sh [--db NAME]
#
# Master prompt section 2 and docs/adr/0001-analytics-warehouse.md require that
# there be no write path from the warehouse into Odoo — not by policy, but
# because the role is structurally incapable of it. A policy can be forgotten;
# a missing privilege cannot.
#
# This script is the evidence for acceptance criterion 8. It asserts:
#   1. SELECT on an Odoo table                       SUCCEEDS
#   2. CREATE TABLE                                  DENIED
#   3. INSERT / UPDATE / DELETE on an Odoo table     DENIED
#   4. CREATE TEMP TABLE                             DENIED  (a temp table is
#                                                     still a write path)
#   5. logical replication slot create/drop          SUCCEEDS (REPLICATION is
#                                                     the half it MUST have)
#
# A denial is a PASS. The script fails if a write succeeds, and it prints the
# verbatim Postgres error for each denial so the evidence is quotable.
# ---------------------------------------------------------------------------
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

DB=""
while [ $# -gt 0 ]; do
    case "$1" in
        --db)   DB="${2:?--db needs a value}"; shift 2 ;;
        --db=*) DB="${1#*=}"; shift ;;
        -h|--help)
            printf 'usage: %s [--db NAME]\n' "$0" >&2; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

require_docker
load_env
DB="${DB:-$ODOO_DB_NAME}"
require_healthy postgres
db_initialised "$DB" || die "database '$DB' has no Odoo schema — run 'make init-db' first."

READER="$WAREHOUSE_READER_USER"
PASS=0
FAIL=0

# Runs SQL as warehouse_reader over TCP against the container's own listener,
# which is what forces a real password authentication rather than the local
# `trust` entry that initdb writes for unix sockets. Proving the denial under
# trust would prove nothing about the deployed path.
as_reader() {
    dc exec -T \
        -e PGPASSWORD="$WAREHOUSE_READER_PASSWORD" \
        postgres psql --no-psqlrc -X -v ON_ERROR_STOP=1 \
        -h 127.0.0.1 -p 5432 -U "$READER" -d "$DB" -tAc "$1" 2>&1
}

hr() { printf '%s\n' "-------------------------------------------------------------------------" >&2; }

expect_success() {
    local label="$1" sql="$2" out rc
    hr; printf '  %s\n  SQL: %s\n' "$label" "$sql" >&2
    set +e; out="$(as_reader "$sql")"; rc=$?; set -e
    if [ $rc -eq 0 ]; then
        printf '  %sPASS%s (expected success) -> %s\n' "$_C_GRN" "$_C_OFF" "${out:-<no rows>}" >&2
        PASS=$((PASS + 1))
    else
        printf '  %sFAIL%s expected success, got:\n%s\n' "$_C_RED" "$_C_OFF" "$out" >&2
        FAIL=$((FAIL + 1))
    fi
}

expect_denied() {
    local label="$1" sql="$2" out rc
    hr; printf '  %s\n  SQL: %s\n' "$label" "$sql" >&2
    set +e; out="$(as_reader "$sql")"; rc=$?; set -e
    if [ $rc -ne 0 ] && printf '%s' "$out" | grep -qi 'permission denied\|must be owner\|no privileges'; then
        printf '  %sPASS%s (correctly denied) -> %s\n' "$_C_GRN" "$_C_OFF" \
            "$(printf '%s' "$out" | tr '\n' ' ' | sed 's/  */ /g')" >&2
        PASS=$((PASS + 1))
    elif [ $rc -ne 0 ]; then
        printf '  %sFAIL%s denied, but not by a privilege check (wrong reason):\n%s\n' \
            "$_C_RED" "$_C_OFF" "$out" >&2
        FAIL=$((FAIL + 1))
    else
        printf '  %sFAIL%s THE WRITE SUCCEEDED. warehouse_reader is not read-only.\n%s\n' \
            "$_C_RED" "$_C_OFF" "$out" >&2
        FAIL=$((FAIL + 1))
    fi
}

log "warehouse_reader read-only proof — role='$READER' database='$DB'"


# ---------------------------------------------------------------------------
# 0. WHO IS ACTUALLY RUNNING THESE CHECKS.
#
# This comes first, and it is answered by the session under test rather than by
# a superuser querying pg_roles about it. Every denial below is evidence only
# if the role we claim to be testing is the one that produced it:
#
#   * connecting as the superuser by mistake makes every write SUCCEED - loud,
#     so that mistake catches itself;
#   * the mirror image is silent. Point an isolation test at a superuser and it
#     still passes, because a SUPERUSER bypasses RLS unconditionally and the
#     policy is never evaluated. The test then proves the query is well-formed,
#     not that isolation works.
#
# Same argument the Security agent wrote into GATE 3 for the semantic-api, and
# it holds here for the same reason: a passing check with no identity line next
# to it carries no information.
#
# rolsuper and friends are booleans, and Postgres renders them 'true'/'false'
# when concatenated with || - not the 't'/'f' that psql's table output shows.
# ---------------------------------------------------------------------------
hr
printf '  0. identity of the session running every check below\n' >&2
IDENTITY="$(as_reader "SELECT current_user || ' superuser=' || rolsuper || ' bypassrls=' || rolbypassrls || ' replication=' || rolreplication FROM pg_roles WHERE rolname = current_user;")"
printf '  %s\n' "$IDENTITY" >&2
if [ "$IDENTITY" = "$READER superuser=false bypassrls=false replication=true" ]; then
    printf '  %sPASS%s not a superuser, cannot bypass RLS, holds REPLICATION\n' "$_C_GRN" "$_C_OFF" >&2
    PASS=$((PASS + 1))
else
    printf '  %sFAIL%s unexpected identity - every result below is meaningless\n' "$_C_RED" "$_C_OFF" >&2
    FAIL=$((FAIL + 1))
fi

# --- the half it must have --------------------------------------------------
expect_success "1. SELECT on an Odoo table" \
    "SELECT count(*) FROM res_partner;"

expect_success "2. SELECT on another Odoo table" \
    "SELECT count(*) FROM ir_module_module WHERE state = 'installed';"

# --- the half it must NOT have ----------------------------------------------
expect_denied "3. CREATE TABLE in public" \
    "CREATE TABLE warehouse_reader_should_not_exist (id int);"

expect_denied "4. INSERT into an Odoo table" \
    "INSERT INTO res_partner (name, active) VALUES ('cdc-should-not-write', true);"

expect_denied "5. UPDATE an Odoo table" \
    "UPDATE res_partner SET name = 'cdc-should-not-write' WHERE id = 1;"

expect_denied "6. DELETE from an Odoo table" \
    "DELETE FROM res_partner WHERE id = 1;"

expect_denied "7. TRUNCATE an Odoo table" \
    "TRUNCATE res_partner;"

# A temp table is a write. Without the REVOKE TEMPORARY in
# scripts/lib/database-baseline.sql this one passes, and "read-only" would be a
# claim rather than a fact.
expect_denied "8. CREATE TEMP TABLE" \
    "CREATE TEMP TABLE warehouse_reader_temp (id int);"

# --- REPLICATION ------------------------------------------------------------
# The role must hold REPLICATION or Phase 3 CDC cannot start at all. Creating
# and immediately dropping a slot proves the attribute is live without leaving
# WAL retention behind — an orphaned slot is exactly what
# max_slot_wal_keep_size exists to bound.
hr
printf '  9. logical replication slot create + drop (REPLICATION attribute)\n' >&2
SLOT="bct_reader_check_$$"
set +e
CREATE_OUT="$(as_reader "SELECT slot_name FROM pg_create_logical_replication_slot('${SLOT}', 'pgoutput');")"
CREATE_RC=$?
set -e
if [ $CREATE_RC -eq 0 ]; then
    printf '  %sPASS%s created slot: %s\n' "$_C_GRN" "$_C_OFF" "$CREATE_OUT" >&2
    PASS=$((PASS + 1))
    DROP_OUT="$(as_reader "SELECT pg_drop_replication_slot('${SLOT}');" || true)"
    REMAIN="$(psql_super "$POSTGRES_DB" -tAc "SELECT count(*) FROM pg_replication_slots WHERE slot_name = '${SLOT}'")"
    if [ "$REMAIN" = "0" ]; then
        printf '  %sPASS%s slot dropped, no WAL retained\n' "$_C_GRN" "$_C_OFF" >&2
        PASS=$((PASS + 1))
    else
        printf '  %sFAIL%s slot %s still exists — it is retaining WAL. Drop it.\n' \
            "$_C_RED" "$_C_OFF" "$SLOT" >&2
        FAIL=$((FAIL + 1))
    fi
else
    printf '  %sFAIL%s could not create a logical slot; REPLICATION is missing:\n%s\n' \
        "$_C_RED" "$_C_OFF" "$CREATE_OUT" >&2
    FAIL=$((FAIL + 1))
fi

# --- attributes -------------------------------------------------------------
hr
printf '  10. pg_roles attributes, as the superuser sees them (cross-check of 0)\n' >&2
psql_super "$POSTGRES_DB" -c \
    "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
       FROM pg_roles WHERE rolname = '$READER';" >&2

hr
if [ "$FAIL" -eq 0 ]; then
    printf '%sALL %d CHECKS PASSED%s — warehouse_reader can read and replicate, and cannot write.\n' \
        "$_C_GRN" "$PASS" "$_C_OFF" >&2
    exit 0
fi
printf '%s%d CHECK(S) FAILED%s (%d passed)\n' "$_C_RED" "$FAIL" "$_C_OFF" "$PASS" >&2
exit 1
