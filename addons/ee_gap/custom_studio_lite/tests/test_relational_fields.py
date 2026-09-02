# -*- coding: utf-8 -*-
"""Relational field types materialise correct ir.model.fields columns."""

from __future__ import annotations

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestRelationalFields(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Field = cls.env["studio.custom.field"]
        cls.IrModel = cls.env["ir.model"]
        cls.partner_model = cls.IrModel.search([("model", "=", "res.partner")], limit=1)
        cls.users_model = cls.IrModel.search([("model", "=", "res.users")], limit=1)

    def test_many2one_field_materialises(self):
        field = self.Field.create(
            {
                "name": "Account Manager",
                "technical_name": "x_studio_account_manager_m2o",
                "model_id": self.partner_model.id,
                "field_type": "many2one",
                "relation_model_id": self.users_model.id,
            }
        )
        field.action_apply()
        field.invalidate_recordset()
        self.assertEqual(field.state, "applied", field.last_error)
        self.assertTrue(field.ir_model_fields_id)
        self.assertEqual(field.ir_model_fields_id.ttype, "many2one")
        self.assertEqual(field.ir_model_fields_id.relation, "res.users")

    def test_many2many_field_materialises_with_link_table(self):
        field = self.Field.create(
            {
                "name": "Watchers",
                "technical_name": "x_studio_watchers_m2m",
                "model_id": self.partner_model.id,
                "field_type": "many2many",
                "relation_model_id": self.users_model.id,
            }
        )
        field.action_apply()
        field.invalidate_recordset()
        self.assertEqual(field.state, "applied", field.last_error)
        self.assertEqual(field.ir_model_fields_id.ttype, "many2many")
        self.assertTrue(field.ir_model_fields_id.relation_table)

    def test_one2many_requires_inverse_field(self):
        from odoo.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            self.Field.create(
                {
                    "name": "Bad O2M",
                    "technical_name": "x_studio_bad_o2m",
                    "model_id": self.partner_model.id,
                    "field_type": "one2many",
                    "relation_model_id": self.users_model.id,
                    # missing relation_field_id
                }
            )

    def test_relational_field_without_target_rejected(self):
        from odoo.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            self.Field.create(
                {
                    "name": "No target",
                    "technical_name": "x_studio_no_target_m2o",
                    "model_id": self.partner_model.id,
                    "field_type": "many2one",
                }
            )
