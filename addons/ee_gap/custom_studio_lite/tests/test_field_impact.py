# -*- coding: utf-8 -*-
"""Field-view impact tracking: dependent_view_ids, rename propagation, delete cascade."""

from __future__ import annotations

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestFieldImpact(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Field = cls.env["studio.custom.field"]
        cls.Cust = cls.env["studio.view.customization"]
        cls.Wizard = cls.env["studio.field.impact.wizard"]
        cls.IrModel = cls.env["ir.model"]
        cls.partner_model = cls.IrModel.search([("model", "=", "res.partner")], limit=1)
        cls.partner_form = cls.env.ref("base.view_partner_form")

        cls.field = cls.Field.create(
            {
                "name": "Impact Field",
                "technical_name": "x_studio_impact_field",
                "model_id": cls.partner_model.id,
                "field_type": "char",
            }
        )
        cls.field.action_apply()

        # Place the field on the partner form so it has a dependent view.
        cls.cust = cls.Cust.create(
            {
                "name": "Partner: impact placement",
                "target_view_id": cls.partner_form.id,
                "operation_ids": [
                    (
                        0,
                        0,
                        {
                            "op_type": "add_field",
                            "field_name": cls.field.technical_name,
                            "anchor_field": "function",
                            "position": "after",
                        },
                    ),
                ],
            }
        )
        cls.cust.action_apply()

    def test_dependent_views_detected(self):
        self.field.invalidate_recordset(["dependent_view_ids", "dependent_view_count"])
        self.assertGreaterEqual(self.field.dependent_view_count, 1)
        self.assertIn(self.cust.inherit_view_id, self.field.dependent_view_ids)

    def test_unlink_blocked_when_dependents_exist(self):
        with self.assertRaises(UserError):
            self.field.unlink()

    def test_unlink_with_force_cascade_succeeds(self):
        wizard = self.Wizard.create(
            {
                "custom_field_id": self.field.id,
                "operation": "delete",
                "view_ids": [(6, 0, self.field.dependent_view_ids.ids)],
                "cascade": True,
            }
        )
        # Refresh the inherit view id from cust before deletion.
        inherit_view = self.cust.inherit_view_id
        wizard.action_confirm()
        # Field should be gone.
        self.assertFalse(self.field.exists())
        # The inherit view's arch should no longer reference the field.
        inherit_view.invalidate_recordset()
        if inherit_view.exists():
            self.assertNotIn("x_studio_impact_field", inherit_view.arch_db or "")

    def test_rename_propagates_to_views(self):
        new_name = "x_studio_impact_field_renamed"
        wizard = self.Wizard.create(
            {
                "custom_field_id": self.field.id,
                "operation": "rename",
                "new_technical_name": new_name,
                "view_ids": [(6, 0, self.field.dependent_view_ids.ids)],
                "cascade": True,
            }
        )
        wizard.action_confirm()
        self.field.invalidate_recordset()
        self.assertEqual(self.field.technical_name, new_name)
        # The materialised inheritance should now reference the new name.
        self.cust.inherit_view_id.invalidate_recordset()
        arch = self.cust.inherit_view_id.arch_db or ""
        self.assertIn(new_name, arch)
        self.assertNotIn('x_studio_impact_field"', arch)
