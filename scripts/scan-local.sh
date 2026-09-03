#!/usr/bin/env bash
# Local static-analysis runner — the developer box runs the SAME scanners as CI,
# so a local "pass" is not a false green (finding S-2). Every scanner runs from a
# DIGEST-PINNED image (image@sha256:...), mounts the repo READ-ONLY, and is
# removed after (--rm). Versions track CI:
#   semgrep  1.175.0  (security/requirements-ci.txt)
#   sqlfluff 4.3.0    (.pre-commit-config.yaml)
#   gitleaks v8.30.1  (ci.yml GITLEAKS_VERSION)
#   hadolint v2.15.1  (.pre-commit-config.yaml, already digest-pinned there)
#   trivy    0.58.0   (aquasecurity/trivy-action v0.36.0)
#
# On a box where the shell lacks the docker group, run:  sg docker -c 'bash scripts/scan-local.sh all'
# Usage: scripts/scan-local.sh [semgrep|hadolint|sqlfluff|gitleaks|trivy|all]
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

SEMGREP="semgrep/semgrep:1.175.0@sha256:b94b53d02fd4a022f9eac4e2af1380f5c3c4c21400e79d3336bdff1d1db5e796"
SQLFLUFF="sqlfluff/sqlfluff:4.3.0@sha256:8003e7099a43661bb972bb29b6c66ef0954545586a047bd88cdbdfb6d758f144"
GITLEAKS="zricethezav/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f"
HADOLINT="hadolint/hadolint:v2.15.1-alpine@sha256:a1d49ae1a4e83c1dbad26b8c1ad7588c8bd1e04f4866b34ad3cac50335198552"
TRIVY="aquasec/trivy:0.58.0@sha256:b88012e2a0a309d6a8a00463d4e63e5e513377fb74eccbc8f9b0f8f81940ebeb"
RO="-v ${REPO}:/src:ro -w /src"
hr(){ printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

scan_semgrep(){ hr "semgrep (.semgrep/)"; docker run --rm $RO "$SEMGREP" semgrep scan --config /src/.semgrep/ --metrics=off /src; }
scan_hadolint(){ hr "hadolint (Dockerfiles)"
  local files; files=$(cd "$REPO" && find . -name Dockerfile -not -path './.git/*' | sed 's#^\./##')
  [ -z "$files" ] && { echo "no Dockerfiles"; return 0; }
  # shellcheck disable=SC2086
  docker run --rm $RO "$HADOLINT" hadolint --config .hadolint.yaml $files; }
scan_sqlfluff(){ hr "sqlfluff (analytics, .sqlfluff)"; docker run --rm $RO "$SQLFLUFF" lint analytics --dialect postgres; }
scan_gitleaks(){ hr "gitleaks (history, .gitleaks.toml)"; docker run --rm $RO "$GITLEAKS" detect --source /src --config /src/.gitleaks.toml --no-banner --redact; }
scan_trivy(){ hr "trivy fs (vuln+secret+misconfig HIGH,CRITICAL, .trivyignore)"
  docker volume create trivy-cache >/dev/null 2>&1 || true
  docker run --rm $RO -v trivy-cache:/root/.cache/trivy "$TRIVY" fs --scanners vuln,secret,misconfig --severity HIGH,CRITICAL --no-progress /src; }

case "${1:-all}" in
  semgrep) scan_semgrep;; hadolint) scan_hadolint;; sqlfluff) scan_sqlfluff;;
  gitleaks) scan_gitleaks;; trivy) scan_trivy;;
  all) scan_semgrep; scan_hadolint; scan_sqlfluff; scan_gitleaks; scan_trivy;;
  *) echo "usage: $0 [semgrep|hadolint|sqlfluff|gitleaks|trivy|all]"; exit 2;;
esac
