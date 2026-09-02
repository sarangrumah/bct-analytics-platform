#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Wrapper for the DESTRUCTIVE cold-start suite. `make test-coldstart` calls it.
#
# This host runs odoo19-platform-*, odoo19-analytics-* and smart-warga-postgres-1
# for other projects. A volume removal that is not scoped to odoo19-bct destroys
# their data irreversibly, and `docker volume rm` has no undo.
#
# WHAT IS STRUCTURAL AND WHAT IS NOT - stated plainly, because the difference
# matters and overstating it would be worse than not having the guard:
#
#   STRUCTURAL (prevention). COMPOSE_PROJECT_NAME, COMPOSE_FILE and
#   COMPOSE_PATH_SEPARATOR are exported before the suite runs. Any bare
#   `docker compose down -v` inside a test therefore resolves to odoo19-bct and
#   CANNOT reach another project - compose has no way to select one without an
#   explicit -p, which would have to be written deliberately.
#
#   NOT STRUCTURAL (detection only). A raw `docker volume rm <name>` or
#   `docker rm -f <name>` in test code bypasses compose entirely, and nothing
#   this script can do from outside will stop it. So it snapshots every volume
#   and container that does NOT belong to this project, and fails loudly if any
#   of them disappeared. That converts a silent irreversible loss into an
#   obvious one. It is a smoke detector, not a fire door.
#
# Also refuses to run when the marker selects zero tests. A destructive target
# that destroys nothing and reports success is the same failure shape as a
# verification step that cannot fail: it is mistaken for evidence.
# ---------------------------------------------------------------------------
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

DRY_RUN=0
for a in "$@"; do
    case "$a" in
        --dry-run) DRY_RUN=1 ;;
    esac
done
# Strip --dry-run before the args reach pytest.
ARGS_OUT=()
for a in "$@"; do [ "$a" = "--dry-run" ] || ARGS_OUT+=("$a"); done
set -- "${ARGS_OUT[@]+"${ARGS_OUT[@]}"}"

require_docker
load_env

PROJECT="${COMPOSE_PROJECT_NAME:-odoo19-bct}"
# Not a credential: it grants nothing and is printed in the refusal message below.
# Named CONFIRM_PHRASE rather than TOKEN so it does not read as a secret - to a
# human or to scripts/scan-secrets.py, which correctly flags such assignments.
CONFIRM_PHRASE="i-understand-this-destroys-the-bct-oltp-data"

# ---------------------------------------------------------------------------
# GATE 0. An explicit, unguessable opt-in.
#
# Requested by the Lead as a deliverable after this target's payload destroyed
# odoo19-bct_pgdata, _odoodata and _redisdata mid-phase. The point is that
# exercising it must be a DELIBERATE act, not something reachable by a stray
# `make test`, a tab-completion, a CI default, or - as actually happened - a
# backtick inside a double-quoted git commit message, which bash expands as
# command substitution before git ever runs.
#
# ASSUME_YES is deliberately NOT sufficient. It is a generic flag other scripts
# in this repo also honour, so a caller that sets it once for an unrelated
# script would silently arm this one. The token is specific to this operation
# and names the data it destroys.
# ---------------------------------------------------------------------------
if [ "${BCT_COLDSTART:-}" != "$CONFIRM_PHRASE" ]; then
    die "refusing: cold start destroys this project's Postgres, Odoo filestore and Redis volumes.
  It is opt-in by an explicit token, not by a generic yes-flag:

      BCT_COLDSTART=$CONFIRM_PHRASE make test-coldstart

  Everything in odoo19-bct is lost and rebuilt: the bct database, the demo seed,
  every CDC replication slot and publication. Other projects on this host are
  scoped out and checked afterwards, but this project is not recoverable from
  this repository."
fi

# --- 1. would this actually run anything? ----------------------------------
log "[1/5] checking that the 'coldstart' marker selects at least one test"
set +e
COLLECTED="$(RUN_COLDSTART=1 "${PYTHON:-python3}" -m pytest tests -c tests/pytest.ini \
    --rootdir "$REPO_ROOT" -m coldstart --collect-only -q 2>&1)"
COLLECT_RC=$?
set -e
if [ "$COLLECT_RC" -eq 5 ] || printf '%s' "$COLLECTED" | grep -q 'no tests collected'; then
    printf '%s\n' "$COLLECTED" | tail -3 >&2
    die "the 'coldstart' marker selects no tests, so this target would destroy this project's volumes and verify nothing.
  tests/pytest.ini declares the marker and tests/run.sh references test_11, but no test carries it yet.
  That file is QA's to write. Refusing to run rather than reporting a destructive success."
fi
N="$(printf '%s' "$COLLECTED" | grep -cE '^tests/.*::' || true)"
info "$N test(s) carry the coldstart marker"

# --- 1b. is CDC in flight? -------------------------------------------------
# A cold start removes pgdata, and every replication slot lives inside it. An
# active slot means a consumer is mid-stream: destroying it strands the
# warehouse at an LSN that no longer exists and forces a full resync. That is
# recoverable, but it should be a decision rather than a surprise.
log "[1b/5] checking for active CDC replication slots"
if docker ps --format '{{.Names}}' | grep -qx "${PROJECT}-postgres"; then
    ACTIVE_SLOTS="$(docker exec "${PROJECT}-postgres" psql -U "${POSTGRES_USER:-odoo}" -tAc         "SELECT string_agg(slot_name, ' ') FROM pg_replication_slots WHERE active" 2>/dev/null | tr -d '
' || true)"
    if [ -n "$ACTIVE_SLOTS" ]; then
        if [ "${BCT_COLDSTART_ALLOW_ACTIVE_SLOTS:-}" = "1" ]; then
            warn "active slot(s) will be destroyed and the warehouse will need a full resync: $ACTIVE_SLOTS"
        else
            die "refusing: CDC is live on slot(s): $ACTIVE_SLOTS
  Destroying pgdata destroys the slot and strands the warehouse mid-stream.
  Stop the consumer first, or accept the resync with:
      BCT_COLDSTART_ALLOW_ACTIVE_SLOTS=1"
        fi
    else
        info "no active replication slots"
    fi
else
    info "postgres is not running; no slots to strand"
fi

# --- 2. snapshot everything that is NOT ours -------------------------------
log "[2/5] recording resources belonging to OTHER projects"
foreign_volumes()   { docker volume ls --format '{{.Name}}' | grep -v "^${PROJECT}[-_]" | sort; }
foreign_containers(){ docker ps -a --format '{{.Names}}'    | grep -v "^${PROJECT}-"    | sort; }

VOL_BEFORE="$(foreign_volumes)"
CON_BEFORE="$(foreign_containers)"
info "$(printf '%s' "$VOL_BEFORE" | grep -c . ) foreign volume(s), $(printf '%s' "$CON_BEFORE" | grep -c . ) foreign container(s) recorded"

# --- 3. consent -------------------------------------------------------------
cat >&2 <<BANNER

  ${_C_YEL}DESTRUCTIVE${_C_OFF}
  Volumes of project '${PROJECT}' will be removed and rebuilt.
  Every database and filestore in this project is lost. Other projects on this
  host are scoped out of compose, and checked afterwards.

BANNER
if [ "$DRY_RUN" -eq 1 ]; then
    log "--dry-run: every gate passed. Nothing was destroyed."
    cat >&2 <<DRY

  To run it for real, non-interactively:

      BCT_COLDSTART=$CONFIRM_PHRASE ASSUME_YES=1 make test-coldstart

  Add BCT_COLDSTART_ALLOW_ACTIVE_SLOTS=1 if CDC is live and you accept the resync.
DRY
    exit 0
fi

confirm "Run the cold-start suite against project '${PROJECT}'?"

# --- 4. run, with the scope forced into the environment --------------------
log "[3/5] running the cold-start suite (compose scoped to '${PROJECT}')"
set +e
(
    export RUN_COLDSTART=1
    export COMPOSE_PROJECT_NAME="$PROJECT"
    export COMPOSE_FILE="compose/odoo.yml:compose/odoo.dev.yml"
    export COMPOSE_PATH_SEPARATOR=":"
    export COMPOSE_IGNORE_ORPHANS=true
    cd "$REPO_ROOT" && bash tests/run.sh -m coldstart "$@"
)
SUITE_RC=$?
set -e

# --- 5. did anything outside this project disappear? -----------------------
log "[4/5] verifying that no other project's resources were touched"
VOL_AFTER="$(foreign_volumes)"
CON_AFTER="$(foreign_containers)"
LOST_VOL="$(comm -23 <(printf '%s\n' "$VOL_BEFORE") <(printf '%s\n' "$VOL_AFTER") | grep . || true)"
LOST_CON="$(comm -23 <(printf '%s\n' "$CON_BEFORE") <(printf '%s\n' "$CON_AFTER") | grep . || true)"

if [ -n "$LOST_VOL" ] || [ -n "$LOST_CON" ]; then
    printf '\n%sCOLLATERAL DAMAGE - resources outside project %s disappeared%s\n' \
        "$_C_RED" "$PROJECT" "$_C_OFF" >&2
    [ -n "$LOST_VOL" ] && printf '  volume gone:    %s\n' $LOST_VOL >&2
    [ -n "$LOST_CON" ] && printf '  container gone: %s\n' $LOST_CON >&2
    die "A docker volume removal has no undo. Find what removed these before running this again."
fi
info "all foreign volumes and containers intact"

log "[5/5] cold-start suite exit code: $SUITE_RC"
exit "$SUITE_RC"
