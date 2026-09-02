#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Generate the demo volume, then give the accounts it creates a password.
#
#     make seed-demo                    default shape (12 months, 2 OUs)
#     make seed-demo ARGS='--months 3'
#     scripts/seed-demo.sh [--db NAME] [--months N] [--seed N] [--dataset NAME]
#
# WHY THIS FILE EXISTS
# --------------------
# `env["demo.seed.generator"].generate()` typed into `odoo shell` was on QA's
# "performed by hand" list after every cold start. There was no target for it,
# so "fresh clone to working stack" was not a documented path - it was tribal
# knowledge, which is exactly how PLAN.md's instance 10 happened.
#
# custom_demo_seed generates NOTHING at install time, by design (manifest
# safeguard 1). Installing it via ODOO_INIT_MODULES is inert; this script is the
# explicit, opt-in call that creates data. `make up-dev` does NOT run it.
#
# ORDERING - the reason the last step is here and not left to the caller.
# generate() creates demo.*@contoh.invalid users with NO password, deliberately:
# "the accounts cannot be logged into until an administrator sets one". So the
# accounts only become usable after scripts/set-dev-passwords.sh has seen them,
# and it can only see them after this has run. Chaining it here means the two
# cannot drift apart; set-dev-passwords.sh is idempotent, so `admin` is a no-op.
#
# generate() is itself idempotent - every record carries an ir.model.data
# external ID and a second run creates nothing - so this whole script is
# re-runnable.
# ---------------------------------------------------------------------------
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

DB=""
MONTHS=""
SEED=""
DATASET=""

usage() {
    cat >&2 <<'USAGE'
usage: scripts/seed-demo.sh [options]

  --db NAME       Database to seed (default: $ODOO_DB_NAME from .env).
  --months N      Months of history to spread the data over (addon default: 12).
  --seed N        RNG seed; same seed, same data (addon default: 20260101).
  --dataset NAME  Independent dataset namespace (addon default: its own).
  -h, --help      This message.

Idempotent: a second run with the same arguments creates nothing. Running it
with DIFFERENT arguments for a dataset that already exists is refused by the
addon, by name - it never silently returns a shape you did not ask for.
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --db) DB="${2:?--db needs a value}"; shift 2 ;;
        --db=*) DB="${1#*=}"; shift ;;
        --months) MONTHS="${2:?--months needs a value}"; shift 2 ;;
        --months=*) MONTHS="${1#*=}"; shift ;;
        --seed) SEED="${2:?--seed needs a value}"; shift 2 ;;
        --seed=*) SEED="${1#*=}"; shift ;;
        --dataset) DATASET="${2:?--dataset needs a value}"; shift 2 ;;
        --dataset=*) DATASET="${1#*=}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1 (try --help)" ;;
    esac
done

for n in MONTHS SEED; do
    v="${!n}"
    [ -z "$v" ] || [[ "$v" =~ ^[0-9]+$ ]] || die "--${n,,} must be a non-negative integer, got '$v'"
done
[ -z "$DATASET" ] || [[ "$DATASET" =~ ^[A-Za-z0-9_-]+$ ]] || \
    die "--dataset must match ^[A-Za-z0-9_-]+\$, got '$DATASET'"

require_docker
load_env
DB="${DB:-$ODOO_DB_NAME}"

require_healthy postgres odoo
db_initialised "$DB" || die "database '$DB' has no Odoo schema. Run 'make up-dev' first."

# Refuse early rather than emit an ImportError-shaped traceback from odoo shell.
state="$(psql_super "$DB" -tAc \
    "SELECT state FROM ir_module_module WHERE name = 'custom_demo_seed'" 2>/dev/null | tr -d '\r' || true)"
if [ "$state" != "installed" ]; then
    die "custom_demo_seed is '${state:-absent}' in '$DB', not 'installed'.
    It is in ODOO_INIT_MODULES in .env.example; an older .env may not have it.
    Install it with:  make install-modules MODULES=custom_demo_seed"
fi

KWARGS=""
[ -z "$MONTHS" ]  || KWARGS="${KWARGS}months=$MONTHS, "
[ -z "$SEED" ]    || KWARGS="${KWARGS}seed=$SEED, "
[ -z "$DATASET" ] || KWARGS="${KWARGS}dataset='$DATASET', "

log "seeding demo data into '$DB' (generate(${KWARGS%, }))"
info "this takes a few minutes on first run; a re-run is fast and creates nothing"

# `odoo shell` rolls back when stdin closes, so the commit is load-bearing.
# Everything is asserted on the SENTINEL, not the exit code: odoo shell exits 0
# for a program that raised nothing and also did nothing.
set +e
out="$(
    cat <<PYSEED | dc exec -T odoo odoo shell -d "$DB" --no-http
counts = env["demo.seed.generator"].generate(${KWARGS%, })
env.cr.commit()
for key in sorted(counts):
    print("SEED %s=%s" % (key, counts[key]))
print("SEED_OK")
PYSEED
)"
rc=$?
set -e

printf '%s\n' "$out" | grep -E '^SEED ' | sed 's/^/    /' >&2 || true

if ! printf '%s\n' "$out" | grep -q '^SEED_OK$'; then
    printf '%s\n' "$out" >&2
    die "odoo shell exited $rc without SEED_OK - demo data was not generated."
fi

# The accounts generate() just created have no password. Give them one now, in
# the same command, so they are usable without a second piece of folklore.
log "applying the dev password to the accounts the seed just created"
"$REPO_ROOT/scripts/set-dev-passwords.sh" --db "$DB"

log "done. 'make check-dev-passwords' now covers the demo users too."
