#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# One-time setup on a fresh clone. Creates nothing that already exists and
# starts no container — `make up-dev` does that.
#
#     make dev-bootstrap
#
# Deliberately ordered so the loudest failure comes first: if the host is not
# capable of running the stack, say so before writing any files.
# ---------------------------------------------------------------------------
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

log "[1/5] checking host prerequisites"

require_docker
info "docker           $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?')"
info "docker compose   $(docker compose version --short 2>/dev/null || echo '?')"
command -v python3 >/dev/null 2>&1 || die "python3 not found. It is required: no script here depends on jq, which is not installed on the target host."
info "python3          $(python3 --version 2>&1 | awk '{print $2}')"
command -v git >/dev/null 2>&1 || warn "git not found; backup manifests will omit commit metadata."

# The port block is 38xxx/35xxx/36xxx/39xxx/33xxx precisely because this host
# already runs other stacks on 8069, 5432, 18xxx and 2xxxx. Checking turns
# "odoo is unhealthy" into "port 38069 is taken by X". It runs after .env
# exists, because .env is where the port numbers come from.
check_port() {
    local port="$1" what="$2" owner
    owner="$(docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null | grep -E ":${port}->" | cut -f1 | head -1 || true)"
    if [ -n "$owner" ]; then
        if [ "$owner" = "$(container_of "$what")" ]; then
            info "port $port -> $what (already ours, fine)"
        else
            die "host port $port is published by container '$owner', which is NOT part of project $COMPOSE_PROJECT_NAME. Change the port in .env rather than stopping that container."
        fi
    else
        info "port $port free ($what)"
    fi
}

log "[2/5] .env"
if [ -f "$ENV_FILE" ]; then
    info ".env exists — merging any new keys from .env.example, keeping existing secrets"
else
    info "no .env — generating one with fresh random secrets"
fi
python3 "$REPO_ROOT/scripts/gen-env-secrets.py" \
    --example "$ENV_EXAMPLE" --out "$ENV_FILE"

load_env

log "[3/5] checking that the reserved host ports are free"
check_port "${ODOO_HOST_HTTP_PORT:-38069}"        odoo
check_port "${ODOO_HOST_LONGPOLLING_PORT:-38072}" odoo
check_port "${POSTGRES_HOST_PORT:-35432}"         postgres
check_port "${REDIS_HOST_PORT:-36379}"            redis

# ---------------------------------------------------------------------------
log "[4/5] directories"
# addons/ is the MOUNT POINT ONLY. It is owned by the Platform-Addons agent;
# Platform-Infra creates the empty directory and writes no module into it.
# Created here rather than committed because git cannot track an empty
# directory, and a bind mount to a missing path is created root-owned by the
# daemon on Linux.
for d in addons backups postgres/conf.d; do
    if [ -d "$REPO_ROOT/$d" ]; then
        info "$d/ exists"
    else
        mkdir -p "$REPO_ROOT/$d"
        info "$d/ created"
    fi
done

# ---------------------------------------------------------------------------
log "[5/5] line endings"
# The dev host is Windows with core.autocrlf=true. A CRLF .sh reaching a Linux
# container dies with 'bad interpreter: /bin/sh^M'. .gitattributes prevents it;
# this verifies the prevention actually took, because a file added before
# .gitattributes existed keeps its CRLF in the index.
crlf_in_index=0
if command -v git >/dev/null 2>&1 && git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    while IFS= read -r line; do
        case "$line" in
            i/crlf*|i/mixed*)
                warn "CRLF in the git index: ${line##*$'\t'}"
                crlf_in_index=$((crlf_in_index + 1)) ;;
        esac
    done < <(git -C "$REPO_ROOT" ls-files --eol -- '*.sh' '*.py' '*.sql' 'Makefile' 'Dockerfile' 2>/dev/null || true)
    if [ "$crlf_in_index" -gt 0 ]; then
        die "$crlf_in_index tracked file(s) carry CRLF in the index. Fix with: git add --renormalize . && git commit"
    fi
    info "no CRLF in the index for *.sh, *.py, *.sql, Makefile, Dockerfile"
else
    info "not a git checkout; skipping the line-ending check"
fi

cat >&2 <<DONE

$(printf '%s' "$_C_GRN")bootstrap complete.$(printf '%s' "$_C_OFF")

  next:   make up-dev
  then:   http://${BIND_ADDRESS:-127.0.0.1}:${ODOO_HOST_HTTP_PORT:-38069}/web/login

  .env holds real generated secrets and is gitignored. It is NOT encrypted;
  the Security agent owns SOPS for anything above dev.
DONE
