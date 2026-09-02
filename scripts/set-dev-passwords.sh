#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Apply $BCT_DEV_USER_PASSWORD to the local Odoo login accounts.
#
#     make set-dev-passwords          apply
#     make check-dev-passwords        assert the live state, change nothing
#     scripts/set-dev-passwords.sh [--db NAME] [--check]
#
# WHY THIS FILE EXISTS
# --------------------
# PLAN.md defect-pattern instance 10. The operator chose "set a local dev
# password". That decision was carried out by hand, once, in a live shell, and
# never became repo: BCT_DEV_USER_PASSWORD appeared only in the untracked .env,
# no target consumed it, and after the documented `make up-dev`
# authenticate('bct','admin','admin') returned uid 2 - Odoo's DEFAULT - while
# .env advertised a 20-character random string that nothing applied.
#
# That is worse than skipping the step, because the file looks like the step was
# done. So the rule this script enforces is not "set a good password"; it is:
#
#     the running database always agrees with .env, and Odoo's default `admin`
#     is never left standing.
#
# WHAT IT TOUCHES
# ---------------
#   * `admin`
#   * every `demo.%@contoh.invalid` account - the users custom_demo_seed creates
#     in generate(). That module deliberately ships them WITHOUT a password
#     ("the accounts cannot be logged into until an administrator sets one"), so
#     setting one here is the administrator step the addon is waiting for, done
#     in a file instead of in someone's scrollback. addons/** is not ours; this
#     operates on the database after the seed has run.
#
# The `demo.` prefix plus the RFC 2606 reserved `@contoh.invalid` domain cannot
# collide with a real account.
#
# ORDERING
# --------
# The demo users exist only after `demo.seed.generator.generate()` has been
# called, which is NOT part of `make up-dev` (custom_demo_seed generates nothing
# at install time, by design). So this script must be, and is:
#
#   * tolerant    - a missing account is reported and skipped, never fatal. A
#                   fresh clone's first `make up-dev` finds `admin` alone.
#   * re-runnable - run it again after seeding and it picks the demo users up,
#                   leaving `admin` untouched.
#
# HASHING
# -------
# It never writes a hash it constructed. It assigns to the ORM field, so Odoo's
# own res.users password setter hashes with the live crypt context; and it tests
# "already correct" with that same context's verify(). A hand-built hash is the
# thing that passes a SQL check and then fails a login.
# ---------------------------------------------------------------------------
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

DB=""
MODE="apply"

usage() {
    cat >&2 <<'USAGE'
usage: scripts/set-dev-passwords.sh [options]

  --db NAME    Database to operate on (default: $ODOO_DB_NAME from .env).
  --check      Do not write. Assert, over XML-RPC as a client would, that
               $BCT_DEV_USER_PASSWORD logs in AND that Odoo's default `admin`
               password is REJECTED. Exits non-zero if either is untrue.
  -h, --help   This message.
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --db) DB="${2:?--db needs a value}"; shift 2 ;;
        --db=*) DB="${1#*=}"; shift ;;
        --check) MODE="check"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1 (try --help)" ;;
    esac
done

require_docker
load_env
DB="${DB:-$ODOO_DB_NAME}"

PW="${BCT_DEV_USER_PASSWORD:-}"

# ---------------------------------------------------------------------------
# The one state nothing here can repair: the key is absent from .env entirely
# (an .env generated before this variable existed). There is no value to apply
# and inventing one would produce a credential nobody can look up. Skip loudly,
# exit 0 - a bring-up must not die over a dev convenience - and name the command
# that fixes it. `make dev-bootstrap` merges new keys from .env.example into an
# existing .env without rotating anything else.
# ---------------------------------------------------------------------------
if [ -z "$PW" ]; then
    warn "BCT_DEV_USER_PASSWORD is not set in .env - no dev password to apply."
    warn "  'admin' therefore keeps whatever password it already has, and on a"
    warn "  fresh database that is Odoo's default, 'admin'."
    warn "  Fix:  make dev-bootstrap    (merges the key in, generates a value)"
    [ "$MODE" = "check" ] && die "cannot verify a credential that is not declared."
    exit 0
fi

if [ "$PW" = "changeme" ]; then
    warn "BCT_DEV_USER_PASSWORD is still the literal placeholder 'changeme'."
    warn "  Applying it anyway: a placeholder you can look up beats Odoo's"
    warn "  default 'admin', which is the state this script exists to end."
    warn "  Fix:  make dev-bootstrap    (generates a real random value)"
fi

require_healthy postgres odoo

if ! db_initialised "$DB"; then
    warn "database '$DB' has no Odoo schema yet - nothing to set. Run 'make up-dev'."
    [ "$MODE" = "check" ] && die "database '$DB' is not initialised."
    exit 0
fi

# The password reaches the container over STDIN, base64-encoded: never in argv
# (visible in `ps` on the host and in the container) and never interpolated into
# Python source, where a quote or a backslash in the value would be a syntax
# error rather than a wrong password. Host python3 is already a hard dependency
# of dev-bootstrap; `base64 -w0` is not portable, this is.
PW_B64="$(BCT_DEV_USER_PASSWORD="$PW" python3 -c \
    'import base64,os;print(base64.b64encode(os.environ["BCT_DEV_USER_PASSWORD"].encode("utf-8")).decode("ascii"))')"

# ===========================================================================
# --check : assert, do not write.
#
# Runs over XML-RPC from the HOST, against the published port, because that is
# the path a human and the login-gateway actually use. Asserting against the
# hash in the table would prove the row, not the login.
#
# It carries a NEGATIVE. PLAN.md standing rule: a check that has never been
# observed to fail is not yet known to work - and "the good password works" is
# green on a stack that accepts BOTH passwords, which is exactly the broken
# state. So `admin`/`admin` MUST be refused for this to pass.
# ===========================================================================
if [ "$MODE" = "check" ]; then
    demo_logins="$(psql_super "$DB" -tAc \
        "SELECT login FROM res_users WHERE login LIKE 'demo.%@contoh.invalid' ORDER BY login" \
        2>/dev/null | tr -d '\r' | tr '\n' ',' || true)"

    CHECK_URL="http://${BIND_ADDRESS:-127.0.0.1}:${ODOO_HOST_HTTP_PORT:-38069}"
    BCT_DEV_PW_B64="$PW_B64" \
    BCT_CHECK_URL="$CHECK_URL" \
    BCT_CHECK_DB="$DB" \
    BCT_CHECK_DEMO="$demo_logins" \
    python3 "$REPO_ROOT/scripts/lib/check-dev-passwords.py"
    exit $?
fi

# ===========================================================================
# apply
# ===========================================================================
log "applying \$BCT_DEV_USER_PASSWORD to admin and demo.%@contoh.invalid in '$DB'"

# `odoo shell` against the LIVE container, not `docker compose run`: other
# agents are using this stack. There is no DDL here - res_users row updates
# only - so the registry deadlock that forces init-db.sh to stop the server
# does not apply.
#
# `odoo shell` ROLLS BACK when stdin closes. The commit in the program below is
# not optional and its absence would be SILENT: every line would print "set"
# and nothing would be written. That is the same shape as the defect being
# fixed, so the program re-reads the committed rows and only then prints
# DEVPW_OK.
set +e
out="$(
    {
        printf '_PW_B64 = "%s"\n' "$PW_B64"
        cat "$REPO_ROOT/scripts/lib/set-dev-passwords.py"
    } | dc exec -T odoo odoo shell -d "$DB" --no-http
)"
rc=$?
set -e

printf '%s\n' "$out" | grep -E '^DEVPW' | sed 's/^/    /' >&2 || true

# Assert the OUTCOME, not the exit code: `odoo shell` exits 0 for a program that
# raised nothing but also did nothing.
if ! printf '%s\n' "$out" | grep -q '^DEVPW_OK$'; then
    printf '%s\n' "$out" >&2
    die "odoo shell exited $rc without DEVPW_OK - no password was verified as applied."
fi

log "done. Odoo's default 'admin' password is no longer accepted in '$DB'."
info "verify:  make check-dev-passwords"
