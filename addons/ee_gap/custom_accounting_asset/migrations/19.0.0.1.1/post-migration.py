# -*- coding: utf-8 -*-
"""Fix the depreciation-posting cron on existing installs.

The cron `cron_post_depreciation` shipped calling a nonexistent method
(`model._cron_post_depreciation()` behind a `hasattr` guard), so it silently
no-opped. The XML record is `noupdate="1"`, so a normal module upgrade will
not correct the code field — do it here. Guarded on the broken code string so
it is idempotent and does not clobber an admin-customised action.
"""


def migrate(cr, version):
    cr.execute(
        """
        UPDATE ir_act_server s
           SET code = 'model._cron_post_due_depreciation()'
          FROM ir_cron c, ir_model_data d
         WHERE s.id = c.ir_actions_server_id
           AND d.model = 'ir.cron'
           AND d.res_id = c.id
           AND d.module = 'custom_accounting_asset'
           AND d.name = 'cron_post_depreciation'
           AND s.code LIKE '%%_cron_post_depreciation%%'
        """
    )
