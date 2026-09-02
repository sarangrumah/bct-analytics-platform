# -*- coding: utf-8 -*-
"""Move the custom_ai_bridge groups onto the module's own privilege.

Groups that share a res.groups.privilege are rendered as ONE pick-one dropdown
on the user form, so saving a user kept a single custom group and silently
dropped every other module's group -- that is what emptied
custom_accounting_reports.group_report_user on prd_retail_begbal and made the
Accounting Reports menu disappear for all 72 users.

The group records carry noupdate="1", so the new privilege_id in the XML is
never applied to tenants where the groups already exist; this re-points them.
Only groups still sitting on the shared privilege (or on none) are touched, so
a deliberate manual re-assignment survives. Idempotent.
"""

MODULE = "custom_ai_bridge"
PRIVILEGE = "res_groups_privilege_ai_bridge"
GROUPS = ["group_custom_ai_user"]


def migrate(cr, version):
    cr.execute(
        """
        UPDATE res_groups g
           SET privilege_id = priv.res_id
          FROM ir_model_data priv, ir_model_data grp
         WHERE priv.module = %s
           AND priv.name = %s
           AND priv.model = 'res.groups.privilege'
           AND grp.module = %s
           AND grp.model = 'res.groups'
           AND grp.name IN %s
           AND g.id = grp.res_id
           AND g.privilege_id IS DISTINCT FROM priv.res_id
           AND (g.privilege_id IS NULL OR g.privilege_id = (
                   SELECT res_id FROM ir_model_data
                    WHERE module = 'custom_core'
                      AND name = 'res_groups_privilege_custom_platform'
                      AND model = 'res.groups.privilege'))
        """,
        (MODULE, PRIVILEGE, MODULE, tuple(GROUPS)),
    )
