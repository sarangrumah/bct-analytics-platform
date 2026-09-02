# -*- coding: utf-8 -*-
"""View customization writes an ir.ui.view inheritance and validates the result."""

from __future__ import annotations

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestViewCustomization(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Field = cls.env["studio.custom.field"]
        cls.Cust = cls.env["studio.view.customization"]
        cls.IrModel = cls.env["ir.model"]
        cls.IrField = cls.env["ir.model.fields"]
        cls.partner_model = cls.IrModel.search([("model", "=", "res.partner")], limit=1)
        # Base partner form view — known stable across Odoo versions.
        cls.partner_form = cls.env.ref("base.view_partner_form")

        # Materialise an x_studio_ char field we can place on the form.
        cls.field = cls.Field.create(
            {
                "name": "Account Manager Note",
                "technical_name": "x_studio_account_manager_note",
                "model_id": cls.partner_model.id,
                "field_type": "char",
            }
        )
        cls.field.action_apply()

    def test_add_field_creates_inheritance_view(self):
        cust = self.Cust.create(
            {
                "name": "Partner: add manager note",
                "target_view_id": self.partner_form.id,
                "operation_ids": [
                    (
                        0,
                        0,
                        {
                            "op_type": "add_field",
                            "field_name": self.field.technical_name,
                            "anchor_field": "function",
                            "position": "after",
                        },
                    ),
                ],
            }
        )
        cust.action_apply()
        cust.invalidate_recordset()
        self.assertEqual(cust.state, "applied", cust.last_error)
        self.assertTrue(cust.inherit_view_id)
        self.assertIn(self.field.technical_name, cust.inherit_view_id.arch_db or "")
        self.assertEqual(cust.inherit_view_id.inherit_id, self.partner_form)

    def test_hide_field_emits_invisible_attribute(self):
        cust = self.Cust.create(
            {
                "name": "Partner: hide website",
                "target_view_id": self.partner_form.id,
                "operation_ids": [
                    (
                        0,
                        0,
                        {
                            "op_type": "hide_field",
                            "field_name": "website",
                        },
                    ),
                ],
            }
        )
        cust.action_apply()
        cust.invalidate_recordset()
        self.assertEqual(cust.state, "applied", cust.last_error)
        arch = cust.inherit_view_id.arch_db or ""
        # The hide_field op emits <attribute name="invisible">1</attribute>
        # inside an <xpath expr="//field[@name='website']"> predicate. The
        # XPath predicate uses single quotes intentionally — see the
        # _render() docstring — so check the rendered field reference
        # without committing to a particular quote style.
        self.assertIn('name="invisible"', arch)
        self.assertIn("@name='website'", arch)

    def test_set_attr_whitelist_blocks_unknown_attribute(self):
        from odoo.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            self.Cust.create(
                {
                    "name": "Bad attr",
                    "target_view_id": self.partner_form.id,
                    "operation_ids": [
                        (
                            0,
                            0,
                            {
                                "op_type": "set_attr",
                                "field_name": "website",
                                "attr_name": "delete_my_database",
                                "attr_value": "1",
                            },
                        ),
                    ],
                }
            )

    def test_apply_with_bad_anchor_marks_error(self):
        cust = self.Cust.create(
            {
                "name": "Partner: bad anchor",
                "target_view_id": self.partner_form.id,
                "operation_ids": [
                    (
                        0,
                        0,
                        {
                            "op_type": "add_field",
                            "field_name": self.field.technical_name,
                            "anchor_field": "this_field_does_not_exist_anywhere",
                            "position": "after",
                        },
                    ),
                ],
            }
        )
        cust.action_apply()
        cust.invalidate_recordset()
        self.assertEqual(cust.state, "error")
        self.assertTrue(cust.last_error)
