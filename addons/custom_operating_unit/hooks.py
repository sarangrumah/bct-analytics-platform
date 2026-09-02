# Part of custom_operating_unit. Licence: LGPL-3.
"""Install-time only seeding.

Why this is a Python hook and not a `<field name="user_ids">` in the group's XML record:

`post_init_hook` is invoked by `odoo/modules/loading.py` only when the update operation is
`install`; on `upgrade` that branch is not taken. So the grant below is applied exactly once, when
the module is first installed, and never again.

The XML alternative does not have that property. With the group record in a `noupdate="0"` block,
every `odoo -u custom_operating_unit` re-applies `user_ids`, silently re-granting the bypass to an
operator who had deliberately revoked it - during routine maintenance, with no message. A control
that un-revokes itself is worse than one that was never applied, because the operator believes the
revocation holds and stops checking.

Moving the whole group record into a `noupdate="1"` block would fix the re-grant but would also
freeze `name`, `comment` and `implied_ids` against future updates, because `noupdate` is a single
flag on one `ir.model.data` row per XML ID - there is no way to have one field of a record be
noupdate and another not. The hook keeps the record fully updatable and the membership one-shot.
"""

import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Grant the Operating Unit bypass to root and admin, once, at install.

    Without it a fresh database is unadministrable: the per-unit record rules are fail-closed, so
    an admin with no allowed units would see only documents that carry no Operating Unit. A real
    deployment is expected to revoke this from day-to-day accounts - see MODULE_KNOWLEDGE.md 5.
    """
    group = env.ref("custom_operating_unit.group_operating_unit_all", raise_if_not_found=False)
    if not group:
        return
    users = env["res.users"].browse()
    for xmlid in ("base.user_root", "base.user_admin"):
        user = env.ref(xmlid, raise_if_not_found=False)
        if user:
            users |= user
    if users:
        group.write({"user_ids": [(4, user.id) for user in users]})
        _logger.info(
            "custom_operating_unit: granted 'All Operating Units' to %s at install "
            "(one-shot; not re-applied on upgrade)",
            ", ".join(users.mapped("login")),
        )
