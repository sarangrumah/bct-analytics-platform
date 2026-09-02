#!/usr/bin/env bash
# =============================================================================
# Negative test for the signing/verification gate.
#
# Owner: Security agent.  Run:  bash security/deploy/test-signing.sh
#
# WHAT THIS PROVES, AND WHAT IT DOES NOT
#   Phase 5 criterion 5 requires `cosign verify-attestation` at deploy time and
#   a DEPLOY THAT FAILS when verification fails, proven by a negative test with
#   an unsigned image.
#
#   PROVEN HERE, by execution against a real local registry:
#     * a signed image verifies                            (control, must pass)
#     * an UNSIGNED image fails verification               (the negative test)
#     * an attestation round-trips and verifies
#     * an image with a signature but NO attestation fails verify-attestation
#     * deploy.sh refuses to swap when the verifier exits non-zero
#
#   NOT PROVEN HERE:
#     cd.yml uses KEYLESS signing, which needs an OIDC token that only a GitHub
#     Actions runner can mint. There is no remote, so that path cannot execute.
#     This harness uses a local key pair instead. The cosign VERB and the gate
#     semantics are identical; the identity source is not. The keyless path is
#     verified by review only, and the report says so.
#
# ISOLATION: a throwaway registry on 127.0.0.1:35500 (outside every port block
# reserved in contract 04), its own container name, removed on every exit path.
# Keys are generated in a temp dir and deleted. Nothing is written to the repo.
# =============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(mktemp -d -t bct-signing-XXXXXX)"
REG_NAME="bct-signing-selftest-registry"
REG_PORT="35500"
REG="127.0.0.1:${REG_PORT}"
export COSIGN_PASSWORD=""

pass=0; fail=0
ok()  { printf '  \033[32mPASS\033[0m %s\n' "$*"; pass=$((pass+1)); }
bad() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; fail=$((fail+1)); }
head_() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

cleanup() {
  docker rm -f "$REG_NAME" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

COSIGN="${COSIGN:-cosign}"
command -v "$COSIGN" >/dev/null 2>&1 || { echo "cosign not on PATH (set COSIGN=/path/to/cosign)"; exit 1; }
echo "cosign: $("$COSIGN" version 2>&1 | grep -i 'GitVersion' | head -1)"

# --- throwaway registry -------------------------------------------------------
head_ "fixtures"
docker rm -f "$REG_NAME" >/dev/null 2>&1 || true
docker run -d --name "$REG_NAME" -p "127.0.0.1:${REG_PORT}:5000" registry:2 >/dev/null 2>&1 \
  || { echo "cannot start local registry"; exit 1; }
for _ in $(seq 1 30); do
  curl -fsS "http://${REG}/v2/" >/dev/null 2>&1 && break
  sleep 1
done
echo "  registry up at ${REG}"

docker pull -q alpine:3.19 >/dev/null 2>&1
docker pull -q alpine:3.20 >/dev/null 2>&1
SIGNED="${REG}/bct/signed"
UNSIGNED="${REG}/bct/unsigned"
docker tag alpine:3.19 "${SIGNED}:v1"; docker push -q "${SIGNED}:v1" >/dev/null 2>&1
docker tag alpine:3.20 "${UNSIGNED}:v1"; docker push -q "${UNSIGNED}:v1" >/dev/null 2>&1

digest_of() { docker buildx imagetools inspect "$1" --format '{{.Manifest.Digest}}' 2>/dev/null; }
SIGNED_DIGEST="$(digest_of "${SIGNED}:v1")"
UNSIGNED_DIGEST="$(digest_of "${UNSIGNED}:v1")"
echo "  signed   -> ${SIGNED_DIGEST}"
echo "  unsigned -> ${UNSIGNED_DIGEST}"

# --- keys ---------------------------------------------------------------------
( cd "$WORK" && "$COSIGN" generate-key-pair >/dev/null 2>&1 ) \
  || { echo "cosign generate-key-pair failed"; exit 1; }
"$COSIGN" signing-config create --out "$WORK/sc-notlog.json" >/dev/null 2>&1
KEY="$WORK/cosign.key"; PUB="$WORK/cosign.pub"
echo "  key pair generated (local key path; cd.yml uses keyless OIDC)"

# --- 1. sign, then verify (control: must PASS) --------------------------------
head_ "1. control - a signed image verifies"
"$COSIGN" sign --yes --key "$KEY" --signing-config "$WORK/sc-notlog.json" "${SIGNED}@${SIGNED_DIGEST}" >/dev/null 2>&1 \
  && ok "signing succeeded" || bad "signing failed"
if "$COSIGN" verify --key "$PUB" --insecure-ignore-tlog=true "${SIGNED}@${SIGNED_DIGEST}" >/dev/null 2>&1; then
  ok "cosign verify PASSES on the signed image"
else
  bad "cosign verify failed on a signed image (control broken - the rest proves nothing)"
fi

# --- 2. THE NEGATIVE TEST: unsigned image must FAIL verification --------------
head_ "2. NEGATIVE TEST - an unsigned image fails verification"
if "$COSIGN" verify --key "$PUB" --insecure-ignore-tlog=true "${UNSIGNED}@${UNSIGNED_DIGEST}" >/dev/null 2>&1; then
  bad "cosign verify PASSED on an UNSIGNED image - the gate is worthless"
else
  ok "cosign verify FAILS on the unsigned image (non-zero exit)"
fi

# --- 3. attestation round-trip ------------------------------------------------
head_ "3. provenance attestation round-trip"
cat > "$WORK/predicate.json" <<'PRED'
{"buildType":"https://github.com/actions/runner","builder":{"id":"selftest"},"invocation":{"configSource":{"uri":"local"}}}
PRED
"$COSIGN" attest --yes --key "$KEY" --signing-config "$WORK/sc-notlog.json" --type slsaprovenance \
  --predicate "$WORK/predicate.json" "${SIGNED}@${SIGNED_DIGEST}" >/dev/null 2>&1 \
  && ok "attestation created" || bad "attest failed"
if "$COSIGN" verify-attestation --key "$PUB" --insecure-ignore-tlog=true --type slsaprovenance \
     "${SIGNED}@${SIGNED_DIGEST}" >/dev/null 2>&1; then
  ok "cosign verify-attestation PASSES on the attested image"
else
  bad "verify-attestation failed on an attested image (control broken)"
fi

# --- 4. signature WITHOUT attestation must fail verify-attestation ------------
head_ "4. NEGATIVE TEST - signed but NOT attested fails verify-attestation"
docker tag alpine:3.19 "${REG}/bct/sigonly:v1"; docker push -q "${REG}/bct/sigonly:v1" >/dev/null 2>&1
SO_DIGEST="$(digest_of "${REG}/bct/sigonly:v1")"
"$COSIGN" sign --yes --key "$KEY" --signing-config "$WORK/sc-notlog.json" "${REG}/bct/sigonly@${SO_DIGEST}" >/dev/null 2>&1
if "$COSIGN" verify-attestation --key "$PUB" --insecure-ignore-tlog=true --type slsaprovenance \
     "${REG}/bct/sigonly@${SO_DIGEST}" >/dev/null 2>&1; then
  bad "verify-attestation PASSED with no attestation present"
else
  ok "verify-attestation FAILS when only a signature exists"
fi
if "$COSIGN" verify --key "$PUB" --insecure-ignore-tlog=true "${REG}/bct/sigonly@${SO_DIGEST}" >/dev/null 2>&1; then
  ok "...while plain verify still passes - so the two checks are not interchangeable"
else
  bad "plain verify failed unexpectedly"
fi

# --- 5. the gate in deploy.sh honours a failing verifier ----------------------
head_ "5. deploy.sh refuses to swap when verification fails"
out="$(DEPLOY_SERVICE=app DEPLOY_IMAGE="$UNSIGNED" DEPLOY_DIGEST="$UNSIGNED_DIGEST" \
      DEPLOY_PROJECT="bct-signing-selftest" DEPLOY_COMPOSE="$WORK/none.yml" \
      BACKUP_CMD='true' \
      VERIFY_CMD="$COSIGN verify --key '$PUB' --insecure-ignore-tlog=true '${UNSIGNED}@${UNSIGNED_DIGEST}'" \
      bash "$HERE/deploy.sh" 2>&1)"; rc=$?
[ $rc -eq 4 ] && ok "deploy.sh exit 4 (verification refused)" || bad "deploy.sh exit $rc (expected 4)"
echo "$out" | grep -q "refusing to deploy an unsigned or unverifiable image" \
  && ok "and says why" || bad "no explanation in output"

printf '\n\033[1m== summary ==\033[0m\n  PASS=%d  FAIL=%d\n' "$pass" "$fail"
if [ "$fail" -ne 0 ]; then echo "  SIGNING_SELFTEST_FAIL"; exit 1; fi
echo "  SIGNING_SELFTEST_OK"
