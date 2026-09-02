"""Set $BCT_DEV_USER_PASSWORD on admin and the custom_demo_seed demo users.

RUNTIME: this is NOT a host script. It is piped into `odoo shell` inside the
odoo container by scripts/set-dev-passwords.sh, which prepends one line:

    _PW_B64 = "<base64 of the password>"

`odoo shell` exec()s stdin with `env` and `self` already bound, then ROLLS BACK
when stdin closes, so the commit below is load-bearing.

Nothing here constructs a password hash. Setting goes through the ORM field, so
Odoo's own res.users setter hashes with the live crypt context; the
already-correct test uses that same context's verify(). A hand-built hash is the
thing that satisfies a SQL check and then fails a login.
"""
import base64

_PW_B64 = globals().get("_PW_B64")
if not _PW_B64:
    raise SystemExit(
        "set-dev-passwords.py: _PW_B64 was not prepended. "
        "Run it through scripts/set-dev-passwords.sh, not by hand."
    )

PASSWORD = base64.b64decode(_PW_B64).decode("utf-8")
ADMIN_LOGIN = "admin"
# `demo.` prefix + the RFC 2606 reserved domain custom_demo_seed uses. `=like`
# passes the pattern to SQL LIKE unescaped, so `%` is the wildcard and `.` is
# literal. This cannot match a real account.
DEMO_LIKE = "demo.%@contoh.invalid"

# active_test=False: a deactivated demo user is still an account with a
# password, and silently skipping it would leave exactly the kind of gap this
# script exists to close.
Users = env["res.users"].with_context(active_test=False)  # noqa: F821 - odoo shell binding
crypt = Users._crypt_context()

targets = Users.search(
    ["|", ("login", "=", ADMIN_LOGIN), ("login", "=like", DEMO_LIKE)],
    order="id",
)


def stored_hash(user_id):
    """Read the hash the way Odoo's own _check_credentials does: straight SQL.

    The `password` field is not readable through the ORM by design.
    """
    env.cr.execute(  # noqa: F821 - odoo shell binding
        "SELECT COALESCE(password, '') FROM res_users WHERE id = %s", (user_id,)
    )
    row = env.cr.fetchone()  # noqa: F821 - odoo shell binding
    return row[0] if row else ""


def already_correct(user_id):
    """True only if Odoo itself would accept PASSWORD for this row.

    An empty column - what custom_demo_seed deliberately leaves behind - and an
    unrecognised scheme both mean "not set", never "matches".
    """
    stored = stored_hash(user_id)
    if not stored:
        return False
    try:
        return bool(crypt.verify(PASSWORD, stored))
    except Exception:  # noqa: BLE001 - any verify error means "not this password"
        return False


changed = 0
unchanged = 0
logins = []

for user in targets:
    logins.append(user.login)
    if already_correct(user.id):
        unchanged += 1
        print("DEVPW %s unchanged" % user.login)
    else:
        # ORM assignment -> res.users' own password setter -> crypt.hash().
        user.password = PASSWORD
        changed += 1
        print("DEVPW %s set" % user.login)

demo_count = len([login for login in logins if login != ADMIN_LOGIN])

# Absence is reported, never fatal. `make up-dev` legitimately runs before
# demo.seed.generator.generate() ever has, and the whole bring-up must not die
# because a fixture module has not been seeded.
if ADMIN_LOGIN not in logins:
    print("DEVPW admin absent")
if demo_count == 0:
    print("DEVPW demo.%@contoh.invalid absent (custom_demo_seed generate() has not run)")

# Only commit when something changed: a second run must write nothing at all,
# not write the same rows again.
if changed:
    env.cr.commit()  # noqa: F821 - odoo shell binding

# Re-read the COMMITTED rows and make Odoo's own verifier agree. Without this
# the script would report success for a transaction odoo shell then rolls back -
# a green check that cannot fail, which is the defect class this fixes.
bad = [user.login for user in targets if not already_correct(user.id)]
if bad:
    print("DEVPW_FAIL not verifiable after commit: %s" % ", ".join(bad))
else:
    print("DEVPW_RESULT changed=%d unchanged=%d demo_users=%d" % (changed, unchanged, demo_count))
    print("DEVPW_OK")
