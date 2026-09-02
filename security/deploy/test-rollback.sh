#!/usr/bin/env bash
# =============================================================================
# Negative-test harness for security/deploy/deploy.sh.
#
# Owner: Security agent.  Run:  bash security/deploy/test-rollback.sh
#
# WHY THIS EXISTS
#   PLAN.md, "the dominant defect pattern in this build": a check that returns
#   the right-looking answer for the wrong reason. Seven catalogued instances.
#   The standing rule is that a check which has never been observed to fail is
#   not yet known to work.
#
#   So every gate in deploy.sh is exercised here by MAKING IT FAIL and asserting
#   the specific exit code and the resulting state - not by watching a happy
#   path go green. A rollback that has only ever been described is not a
#   rollback (Phase 5 brief, criterion 8).
#
# WHAT IS REAL HERE
#   Real Docker, real images, two real registry digests of the SAME repository,
#   a real compose healthcheck, a real failure, a real rollback. What is NOT
#   real: the SSH transport to a VPS and the production compose file. Those
#   cannot be exercised without a deploy target and are reported as unverified.
#
# ISOLATION
#   Everything runs in its own compose project (bct-deploy-selftest) on its own
#   network with no volumes and no published ports. It cannot touch odoo19-bct,
#   odoo19-platform, odoo19-analytics or smart-warga. Cleanup is scoped to that
#   project name and runs on every exit path.
# =============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PROJECT="bct-deploy-selftest"
WORK="$(mktemp -d -t bct-deploy-test-XXXXXX)"
COMPOSE="$WORK/compose.yml"

# Two digests of the SAME repository, so a rollback is a real digest change.
# alpine:3.19 satisfies the healthcheck; alpine:3.20 does not. The healthcheck
# greps the release file, so "unhealthy" is produced by the image actually being
# different - not by a flag we set to make the test convenient.
IMAGE="docker.io/library/alpine"
HEALTHY_TAG="3.19"
BROKEN_TAG="3.20"

pass=0; fail=0
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$*"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; fail=$((fail+1)); }
head_() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

cleanup() {
  docker compose -p "$PROJECT" -f "$COMPOSE" down --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

# --- fixtures ----------------------------------------------------------------
head_ "fixtures"
for t in "$HEALTHY_TAG" "$BROKEN_TAG"; do
  docker pull -q "${IMAGE}:${t}" >/dev/null 2>&1 || { echo "cannot pull ${IMAGE}:${t}"; exit 1; }
done
HEALTHY_DIGEST="$(docker inspect --format '{{index .RepoDigests 0}}' "${IMAGE}:${HEALTHY_TAG}" | sed 's/.*@//')"
BROKEN_DIGEST="$(docker inspect --format '{{index .RepoDigests 0}}' "${IMAGE}:${BROKEN_TAG}" | sed 's/.*@//')"
echo "  healthy ${HEALTHY_TAG} -> ${HEALTHY_DIGEST}"
echo "  broken  ${BROKEN_TAG} -> ${BROKEN_DIGEST}"
[ "$HEALTHY_DIGEST" = "$BROKEN_DIGEST" ] && { echo "digests identical - test would prove nothing"; exit 1; }

cat > "$COMPOSE" <<COMPOSEEOF
services:
  app:
    image: ${IMAGE}:${HEALTHY_TAG}
    command: ["sleep", "600"]
    healthcheck:
      test: ["CMD", "grep", "-q", "^${HEALTHY_TAG}", "/etc/alpine-release"]
      interval: 2s
      timeout: 2s
      retries: 3
      start_period: 1s
COMPOSEEOF

running_digest() {
  local cid; cid="$(docker compose -p "$PROJECT" -f "$COMPOSE" ps -q app 2>/dev/null)"
  [ -z "$cid" ] && { echo "none"; return; }
  local iid; iid="$(docker inspect --format '{{.Image}}' "$cid")"
  docker inspect --format '{{index .RepoDigests 0}}' "$iid" 2>/dev/null | sed 's/.*@//'
}

run_deploy() {
  DEPLOY_PROJECT="$PROJECT" DEPLOY_COMPOSE="$COMPOSE" DEPLOY_SERVICE=app \
  DEPLOY_IMAGE="$IMAGE" HEALTH_TIMEOUT="${HT:-25}" HEALTH_INTERVAL=2 \
  DEPLOY_DIGEST="$1" BACKUP_CMD="${BACKUP_CMD-true}" VERIFY_CMD="${VERIFY_CMD-true}" \
  MIGRATE_CMD="${MIGRATE_CMD:-}" HEALTH_CMD="${HEALTH_CMD:-}" \
  ROLLBACK_ENABLED="${ROLLBACK_ENABLED:-1}" \
  bash "$HERE/deploy.sh" 2>&1
}

# --- 1. baseline: a good deploy must actually succeed -------------------------
head_ "1. baseline - healthy digest deploys and the gate goes green"
out="$(run_deploy "$HEALTHY_DIGEST")"; rc=$?
[ $rc -eq 0 ] && ok "exit 0" || { bad "exit $rc (expected 0)"; echo "$out" | tail -12; }
[ "$(running_digest)" = "$HEALTHY_DIGEST" ] && ok "running digest is the healthy one" || bad "running digest is $(running_digest)"

# --- 2. THE ROLLBACK ---------------------------------------------------------
head_ "2. health-gate failure rolls back to the previous digest"
echo "  deploying ${BROKEN_TAG} (its healthcheck greps for ${HEALTHY_TAG} and cannot pass)"
out="$(run_deploy "$BROKEN_DIGEST")"; rc=$?
[ $rc -eq 5 ] && ok "exit 5 - failed deploy reported as failure, not success" || bad "exit $rc (expected 5)"
echo "$out" | grep -q "ROLLING BACK to ${HEALTHY_DIGEST}" && ok "announced rollback to the recorded previous digest" || bad "no rollback announcement"
now="$(running_digest)"
[ "$now" = "$HEALTHY_DIGEST" ] && ok "PREVIOUS DIGEST RESTORED and running" || bad "running digest is $now, expected $HEALTHY_DIGEST"
echo "$out" | grep -q "rollback to ${HEALTHY_DIGEST} is healthy" && ok "post-rollback health gate re-run and passed" || bad "rollback health not re-verified"
printf '\n  ---- deploy.sh output, rollback run ----\n'; echo "$out" | sed 's/^/  | /'

# --- 3. backup failure must abort BEFORE any swap ----------------------------
head_ "3. pre-deploy backup failure aborts before anything is swapped"
before="$(running_digest)"
out="$(BACKUP_CMD='false' run_deploy "$BROKEN_DIGEST")"; rc=$?
[ $rc -eq 3 ] && ok "exit 3" || bad "exit $rc (expected 3)"
[ "$(running_digest)" = "$before" ] && ok "running digest UNCHANGED - no swap happened" || bad "digest changed despite backup failure"
echo "$out" | grep -q "aborting BEFORE any swap" && ok "said so explicitly" || bad "no abort message"

# --- 4. signature verification failure must abort before any swap ------------
head_ "4. unsigned / unverifiable image is refused"
before="$(running_digest)"
out="$(VERIFY_CMD='false' run_deploy "$BROKEN_DIGEST")"; rc=$?
[ $rc -eq 4 ] && ok "exit 4" || bad "exit $rc (expected 4)"
[ "$(running_digest)" = "$before" ] && ok "running digest UNCHANGED - unsigned image never ran" || bad "digest changed despite failed verification"

# --- 5. an EMPTY verifier must fail closed, not pass -------------------------
head_ "5. empty verifier fails closed (the 'check that cannot fail' case)"
out="$(VERIFY_CMD='' run_deploy "$BROKEN_DIGEST")"; rc=$?
[ $rc -eq 4 ] && ok "exit 4 - refuses to deploy rather than skipping verification" || bad "exit $rc (expected 4)"
echo "$out" | grep -q "empty verifier that returns success" && ok "names the failure mode" || bad "no explanation"

# --- 6. a tag instead of a digest must be rejected ---------------------------
head_ "6. deploying a tag instead of a digest is rejected"
out="$(run_deploy "latest")"; rc=$?
[ $rc -eq 2 ] && ok "exit 2" || bad "exit $rc (expected 2)"
echo "$out" | grep -q "never by tag" && ok "explains why" || bad "no explanation"

# --- 7. migration failure must reach the rollback path -----------------------
head_ "7. migration failure triggers rollback, not a silent pass"
before="$(running_digest)"
out="$(MIGRATE_CMD='false' run_deploy "$BROKEN_DIGEST")"; rc=$?
[ $rc -eq 5 ] && ok "exit 5" || bad "exit $rc (expected 5)"
[ "$(running_digest)" = "$before" ] && ok "rolled back to $before" || bad "left on $(running_digest)"

# --- 8. idempotent re-deploy of the SAME digest ------------------------------
head_ "8. re-deploying the running digest is a no-op that still passes"
out="$(run_deploy "$HEALTHY_DIGEST")"; rc=$?
[ $rc -eq 0 ] && ok "exit 0" || bad "exit $rc (expected 0)"
echo "$out" | grep -q "already running" && ok "recognised as already running" || bad "did not notice"

# --- summary -----------------------------------------------------------------
printf '\n\033[1m== summary ==\033[0m\n  PASS=%d  FAIL=%d\n' "$pass" "$fail"
if [ "$fail" -ne 0 ]; then echo "  ROLLBACK_SELFTEST_FAIL"; exit 1; fi
echo "  ROLLBACK_SELFTEST_OK"
