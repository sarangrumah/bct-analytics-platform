#!/usr/bin/env bash
# =============================================================================
# Digest-pinned deploy with a health gate and automatic rollback.
#
# Owner: Security agent. Called by .github/workflows/cd.yml on the deploy host.
#
# WHY THIS IS A SCRIPT AND NOT INLINE YAML
#   A rollback that only exists as workflow steps can never be tested without a
#   remote, a runner and a production host. As a script it can be exercised
#   locally against real containers, which is the difference between "rollback
#   documented" and "rollback demonstrated" (Phase 5 brief, criterion 8).
#   security/deploy/test-rollback.sh does exactly that.
#
# ORDER IS THE CONTROL. Each step gates the next, and nothing is swapped until
# the backup and the signature both pass:
#
#   1. resolve the currently running digest        (what we roll back TO)
#   2. pre-deploy backup                            must succeed or we abort
#   3. cosign verify-attestation on the new digest  unsigned/unverifiable aborts
#   4. swap to the new digest                       by digest, never a tag
#   5. idempotent migration                         re-runnable, no-op second time
#   6. health gate                                  compose health + app + dbt recon + alerting
#   7. on any health failure: restore step 1's digest, re-run the health gate,
#      and exit non-zero regardless of whether the rollback itself succeeded.
#      A rolled-back deploy is still a failed deploy.
#
# NO jq. `docker inspect --format` uses Go templates; jq is absent on the
# operator host and no tool here may depend on it.
#
# Configuration is by environment variable so every external command can be
# pointed at a harness for testing. Defaults are the real production commands;
# the test harness overrides them explicitly and says so in its output.
# =============================================================================
set -euo pipefail

# --- required -----------------------------------------------------------------
: "${DEPLOY_SERVICE:?DEPLOY_SERVICE is required (compose service name)}"
: "${DEPLOY_IMAGE:?DEPLOY_IMAGE is required (registry path, no tag)}"
: "${DEPLOY_DIGEST:?DEPLOY_DIGEST is required (sha256:... - deploys are by digest, never by tag)}"

# --- overridable --------------------------------------------------------------
DEPLOY_PROJECT="${DEPLOY_PROJECT:-odoo19-bct}"
DEPLOY_COMPOSE="${DEPLOY_COMPOSE:-compose/odoo.yml}"
DEPLOY_TENANT="${DEPLOY_TENANT:-bct}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-5}"

BACKUP_CMD="${BACKUP_CMD:-scripts/tenant-backup.sh ${DEPLOY_TENANT}}"
VERIFY_CMD="${VERIFY_CMD:-}"          # set by cd.yml to the cosign invocation
MIGRATE_CMD="${MIGRATE_CMD:-}"        # idempotent Odoo module update
HEALTH_CMD="${HEALTH_CMD:-}"          # app-level health, dbt recon, alerting
ROLLBACK_ENABLED="${ROLLBACK_ENABLED:-1}"

log()  { printf '%s [deploy] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
fail() { printf '%s [deploy] FAIL: %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }

compose() { docker compose -p "$DEPLOY_PROJECT" -f "$DEPLOY_COMPOSE" "$@"; }

# Guard against the single most common deploy mistake this script exists to stop.
case "$DEPLOY_DIGEST" in
  sha256:*) ;;
  *) fail "DEPLOY_DIGEST must be a sha256: digest, got '${DEPLOY_DIGEST}'. Deploys are by digest, never by tag - a tag can be moved after it was scanned and signed."
     exit 2 ;;
esac

# -----------------------------------------------------------------------------
# 1. What is running now? This is the rollback target, captured BEFORE anything
#    changes. If we cannot determine it we do not proceed: a deploy with no
#    known-good digest to return to is a one-way door.
# -----------------------------------------------------------------------------
previous_digest=""
container="$(compose ps -q "$DEPLOY_SERVICE" 2>/dev/null || true)"
if [ -n "$container" ]; then
  image_id="$(docker inspect --format '{{.Image}}' "$container" 2>/dev/null || true)"
  if [ -n "$image_id" ]; then
    previous_digest="$(docker inspect --format '{{index .RepoDigests 0}}' "$image_id" 2>/dev/null || true)"
    previous_digest="${previous_digest##*@}"
  fi
fi

if [ -n "$previous_digest" ]; then
  log "currently running: ${previous_digest}"
else
  log "no running container for '${DEPLOY_SERVICE}' - first deploy, rollback unavailable"
  if [ "$ROLLBACK_ENABLED" = "1" ]; then
    log "NOTE: a health-gate failure on a first deploy cannot roll back. It will stop the service and exit non-zero."
  fi
fi

if [ "$previous_digest" = "$DEPLOY_DIGEST" ]; then
  log "requested digest is already running; nothing to swap"
fi

# -----------------------------------------------------------------------------
# 2. Pre-deploy backup. Must succeed BEFORE anything is swapped (criterion 7).
#    Not after, not in parallel: the backup exists to make the swap reversible,
#    so a backup taken after the swap protects nothing.
# -----------------------------------------------------------------------------
log "pre-deploy backup: ${BACKUP_CMD}"
if ! eval "$BACKUP_CMD"; then
  fail "pre-deploy backup failed - aborting BEFORE any swap. Nothing has changed."
  exit 3
fi
log "backup OK"

# -----------------------------------------------------------------------------
# 3. Signature and provenance. An unsigned or unverifiable image never runs.
# -----------------------------------------------------------------------------
if [ -n "$VERIFY_CMD" ]; then
  log "verifying signature and provenance of ${DEPLOY_IMAGE}@${DEPLOY_DIGEST}"
  if ! eval "$VERIFY_CMD"; then
    fail "cosign verification failed - refusing to deploy an unsigned or unverifiable image. Nothing has changed."
    exit 4
  fi
  log "signature and provenance OK"
else
  fail "VERIFY_CMD is empty. Refusing to deploy without verifying the image."
  fail "This is deliberate: an empty verifier that returns success is the exact"
  fail "failure this phase exists to prevent (PLAN.md, 'a check that cannot fail')."
  exit 4
fi

# -----------------------------------------------------------------------------
# 4. Swap, by digest.
# -----------------------------------------------------------------------------
log "pulling ${DEPLOY_IMAGE}@${DEPLOY_DIGEST}"
docker pull "${DEPLOY_IMAGE}@${DEPLOY_DIGEST}"

# The digest is applied through a generated compose OVERRIDE rather than by
# editing compose/odoo.yml. That file belongs to Platform-Infra, and a deploy
# that rewrites another agent's tracked file - on the production host, mid-swap -
# is both a path violation and an excellent way to lose the ability to roll back.
DEPLOY_OVERRIDE="${DEPLOY_OVERRIDE:-$(mktemp -t bct-deploy-override-XXXXXX.yml)}"
trap 'rm -f "$DEPLOY_OVERRIDE"' EXIT

swap_to() {
  local digest="$1"
  log "swapping '${DEPLOY_SERVICE}' to ${digest}"
  printf 'services:\n  %s:\n    image: %s@%s\n' \
    "$DEPLOY_SERVICE" "$DEPLOY_IMAGE" "$digest" > "$DEPLOY_OVERRIDE"
  docker compose -p "$DEPLOY_PROJECT" -f "$DEPLOY_COMPOSE" -f "$DEPLOY_OVERRIDE" \
    up -d --no-deps --force-recreate "$DEPLOY_SERVICE"
}
swap_to "$DEPLOY_DIGEST"

# -----------------------------------------------------------------------------
# 5. Migration. Must be idempotent: running it twice is a no-op (criterion 9).
# -----------------------------------------------------------------------------
migration_ok=1
if [ -n "$MIGRATE_CMD" ]; then
  log "migration: ${MIGRATE_CMD}"
  if ! eval "$MIGRATE_CMD"; then
    fail "migration failed - treated as a health-gate failure so the rollback path runs"
    migration_ok=0
  fi
fi

# -----------------------------------------------------------------------------
# 6. Health gate.
# -----------------------------------------------------------------------------
health_gate() {
  local deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
  local cid state

  while :; do
    cid="$(compose ps -q "$DEPLOY_SERVICE" 2>/dev/null || true)"
    if [ -z "$cid" ]; then
      state="no-container"
    else
      state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo unknown)"
    fi

    case "$state" in
      healthy|running)
        log "container state: ${state}"
        break ;;
      unhealthy|exited|dead|no-container)
        fail "container state: ${state}"
        return 1 ;;
    esac

    if [ "$(date +%s)" -ge "$deadline" ]; then
      fail "health gate timed out after ${HEALTH_TIMEOUT}s in state '${state}'"
      return 1
    fi
    sleep "$HEALTH_INTERVAL"
  done

  # Application-level checks: HTTP health, dbt reconciliation, alerting liveness.
  if [ -n "$HEALTH_CMD" ]; then
    log "application health gate: ${HEALTH_CMD}"
    if ! eval "$HEALTH_CMD"; then
      fail "application health gate failed"
      return 1
    fi
  fi
  return 0
}

log "running health gate (timeout ${HEALTH_TIMEOUT}s)"
if [ "$migration_ok" = "1" ] && health_gate; then
  log "HEALTH GATE PASSED - deploy of ${DEPLOY_DIGEST} complete"
  exit 0
fi

# -----------------------------------------------------------------------------
# 7. Rollback.
# -----------------------------------------------------------------------------
fail "health gate FAILED for ${DEPLOY_DIGEST}"

if [ "$ROLLBACK_ENABLED" != "1" ]; then
  fail "rollback disabled by ROLLBACK_ENABLED=${ROLLBACK_ENABLED}; leaving the failed deploy in place"
  exit 5
fi
if [ -z "$previous_digest" ]; then
  fail "no previous digest recorded - cannot roll back (first deploy). Stopping the service."
  compose stop "$DEPLOY_SERVICE" || true
  exit 5
fi

log "ROLLING BACK to ${previous_digest}"
if ! swap_to "$previous_digest"; then
  fail "ROLLBACK SWAP FAILED - service may be down. Manual intervention required."
  exit 6
fi

if health_gate; then
  log "rollback to ${previous_digest} is healthy"
  fail "deploy of ${DEPLOY_DIGEST} FAILED and was rolled back. Exiting non-zero:"
  fail "a rolled-back deploy is a failed deploy, and CD must not report success."
  exit 5
fi

fail "ROLLBACK IS ALSO UNHEALTHY - the previous digest did not come back cleanly."
fail "Manual intervention required. Restore from the pre-deploy backup taken above."
exit 6
