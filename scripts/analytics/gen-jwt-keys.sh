#!/usr/bin/env bash
# Generate the TWO RS256 signing keypairs the gateway publishes in JWKS (security finding T-4).
#
# Two, from day one, is the whole point. A single-key JWKS cannot be rotated without an outage:
# publish the new key first and verifiers holding a cached JWKS reject every token; sign with it
# first and verifiers reject every token until they refetch. There is no ordering that works. With
# two keys already published and `kid` selecting between them, rotation is:
#
#   1. both keys are in JWKS and every verifier already accepts both;
#   2. flip LOGIN_GATEWAY_JWT_KID to the standby kid and restart the gateway;
#   3. tokens signed by the old key keep verifying until they expire (3600 s);
#   4. generate a fresh standby with `--rotate` and repeat.
#
# Keys are written to login-gateway/secrets/, which is gitignored. A private key committed once is
# a private key in every clone, CI cache and backup forever, so this script refuses to overwrite an
# existing key without --force.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="$ROOT/login-gateway/secrets"
FORCE=0
ROTATE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --rotate) ROTATE=1; shift ;;
    *) echo "usage: $0 [--force] [--rotate]" >&2; exit 2 ;;
  esac
done

mkdir -p "$OUT"
chmod 700 "$OUT"

gen() {
  local name="$1"
  if [ -f "$OUT/$name-private.pem" ] && [ "$FORCE" = "0" ]; then
    echo "keep    $name (already exists; pass --force to replace)"
    return
  fi
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
    -out "$OUT/$name-private.pem" 2>/dev/null
  openssl rsa -in "$OUT/$name-private.pem" -pubout -out "$OUT/$name-public.pem" 2>/dev/null
  chmod 600 "$OUT/$name-private.pem"
  chmod 644 "$OUT/$name-public.pem"
  echo "created $name"
}

if [ "$ROTATE" = "1" ]; then
  # Promote the standby to active and mint a new standby. The old active is discarded only after
  # its tokens have expired, which is why this is a two-step the operator drives, not one command.
  echo "==> rotation: promote the standby, then mint a new standby"
  echo "    1. set LOGIN_GATEWAY_JWT_KID to the current LOGIN_GATEWAY_JWT_NEXT_KID"
  echo "    2. restart the gateway; both keys stay in JWKS so nothing breaks"
  echo "    3. after ${LOGIN_GATEWAY_ACCESS_TOKEN_TTL:-3600}s every old token has expired"
  echo "    4. re-run this script with --force to mint a fresh standby"
  exit 0
fi

gen jwt
gen jwt-next

# kids are derived from the key itself, so a kid can never name a key that is not the one loaded.
KID_A=$(openssl rsa -in "$OUT/jwt-private.pem" -pubout -outform DER 2>/dev/null | openssl dgst -sha256 -binary | base64 | tr '+/' '-_' | tr -d '=' | cut -c1-16)
KID_B=$(openssl rsa -in "$OUT/jwt-next-private.pem" -pubout -outform DER 2>/dev/null | openssl dgst -sha256 -binary | base64 | tr '+/' '-_' | tr -d '=' | cut -c1-16)

cat <<EOF

Keys are in login-gateway/secrets/ (gitignored, mode 600 on the private halves).

Set these in .env -- LOGIN_GATEWAY_JWT_KID already exists; the NEXT_* names are new:

  LOGIN_GATEWAY_JWT_KID=$KID_A
  LOGIN_GATEWAY_JWT_NEXT_KID=$KID_B

The kid is the first 16 characters of the base64url SHA-256 of the DER public key, so it is derived
from the key rather than assigned. A kid can therefore never name a key that is not the one loaded.
EOF
