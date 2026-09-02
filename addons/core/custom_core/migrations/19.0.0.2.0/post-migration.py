# -*- coding: utf-8 -*-
"""Put the shared "Custom Platform" privilege in its module category.

Sibling modules stop reusing this privilege in this release and get one each,
all pointing at ``module_category_custom_platform`` so the user form still
shows them side by side. The privilege record itself is noupdate="1", so the
category_id added to the XML never reaches existing tenants without this.
"""


def migrate(cr, version):
    cr.execute(
        """
        UPDATE res_groups_privilege p
           SET category_id = cat.res_id
          FROM ir_model_data priv, ir_model_data cat
         WHERE priv.module = 'custom_core'
           AND priv.name = 'res_groups_privilege_custom_platform'
           AND priv.model = 'res.groups.privilege'
           AND cat.module = 'custom_core'
           AND cat.name = 'module_category_custom_platform'
           AND cat.model = 'ir.module.category'
           AND p.id = priv.res_id
           AND p.category_id IS NULL
        """
    )
