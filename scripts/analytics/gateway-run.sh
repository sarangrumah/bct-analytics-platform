#!/usr/bin/env bash
# Run the login gateway.
#
# Reserved port 38120 (contract 04 §4), bound to 127.0.0.1 only. Signing keys are MOUNTED read-only
# from login-gateway/secrets/, never baked into the image: a key in a layer is a key in every
# registry copy and every `docker save` tarball.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
set -a; . "$ROOT/.env"; set +a

NAME="odoo19-bct-login-gateway"
DETACH=""
while [ $# -gt 0 ]; do
  case "$1" in
    --detach) DETACH="-d"; shift ;;
    *) break ;;
  esac
done

if [ ! -f "$ROOT/login-gateway/secrets/jwt-private.pem" ]; then
  echo "no signing keys; run scripts/analytics/gen-jwt-keys.sh first" >&2
  exit 1
fi

docker rm -f "$NAME" >/dev/null 2>&1 || true

# MSYS_NO_PATHCONV is scoped to this one invocation, never exported (contract 04 section 11).
# Git Bash rewrites POSIX-looking arguments AND exported values before a native .exe sees them, so
# without this both the `-v ...:/run/secrets` target and the value of
# LOGIN_GATEWAY_JWT_PRIVATE_KEY_PATH arrive as `C:/Program Files/Git/run/secrets/...`. Exporting it
# globally instead breaks every other native tool on the host, which is why contract 04 scopes it.
# shellcheck disable=SC2086
exec env MSYS_NO_PATHCONV=1 docker run --rm $DETACH --name "$NAME" \
  --network odoo19-bct_bct \
  -p "${BIND_ADDRESS:-127.0.0.1}:${LOGIN_GATEWAY_HOST_PORT:-38120}:8080" \
  --security-opt no-new-privileges \
  --cap-drop ALL \
  --read-only \
  -v "$ROOT/login-gateway/secrets:/run/secrets:ro" \
  -e LOGIN_GATEWAY_JWT_ALGORITHM -e LOGIN_GATEWAY_JWT_KID -e LOGIN_GATEWAY_JWT_NEXT_KID \
  -e LOGIN_GATEWAY_JWT_PRIVATE_KEY_PATH -e LOGIN_GATEWAY_JWT_NEXT_PRIVATE_KEY_PATH \
  -e LOGIN_GATEWAY_JWT_ISSUER -e LOGIN_GATEWAY_JWT_AUDIENCE \
  -e LOGIN_GATEWAY_ACCESS_TOKEN_TTL -e LOGIN_GATEWAY_REFRESH_TOKEN_TTL \
  -e LOGIN_GATEWAY_REFRESH_COOKIE_NAME -e LOGIN_GATEWAY_COOKIE_SECURE \
  -e LOGIN_GATEWAY_ODOO_URL -e LOGIN_GATEWAY_ALLOWED_DATABASES \
  -e LOGIN_GATEWAY_RATE_LIMIT_MAX_ATTEMPTS -e LOGIN_GATEWAY_RATE_LIMIT_WINDOW_SECONDS \
  -e LOGIN_GATEWAY_RATE_LIMIT_LOCKOUT_SECONDS \
  odoo19-bct-login-gateway:local "$@"
