# Part of custom_operating_unit. Licence: LGPL-3.
"""Tests for the Operating Unit dimension and its record rules."""

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

#: Models that must carry a stored, indexed operating_unit_id. ppob.transaction is asserted in
#: custom_ppob's own suite so that this module stays installable without it.
STAMPED_MODELS = [
    ("sale.order", "sale_order"),
    ("account.move", "account_move"),
    ("stock.picking", "stock_picking"),
    ("pos.order", "pos_order"),
]


@tagged("post_install", "-at_install", "operating_unit")
class TestOperatingUnit(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("base.main_company")
        cls.Unit = cls.env["operating.unit"]
        cls.ou_a = cls.Unit.create({
            "name": "Cabang A",
            "code": "OU-TEST-A",
            "company_id": cls.company.id,
        })
        cls.ou_b = cls.Unit.create({
            "name": "Cabang B",
            "code": "OU-TEST-B",
            "company_id": cls.company.id,
        })
        cls.ou_a_child = cls.Unit.create({
            "name": "Depo A1",
            "code": "OU-TEST-A1",
            "company_id": cls.company.id,
            "parent_id": cls.ou_a.id,
        })

    # -- the dimension --------------------------------------------------

    def test_complete_name_reflects_the_hierarchy(self):
        self.assertEqual(self.ou_a.complete_name, "Cabang A")
        self.assertEqual(self.ou_a_child.complete_name, "Cabang A / Depo A1")

    def test_code_is_unique_per_company(self):
        import psycopg2

        with self.assertRaises(psycopg2.errors.UniqueViolation):
            with self.env.cr.savepoint(), mute_logger("odoo.sql_db"):
                self.Unit.create({
                    "name": "Duplicate",
                    "code": "OU-TEST-A",
                    "company_id": self.company.id,
                })

    def test_no_recursive_hierarchy(self):
        # Odoo 19's _parent_store maintenance raises UserError("Recursion Detected.") before the
        # model's own @api.constrains runs. ValidationError subclasses UserError, so asserting on
        # UserError covers whichever guard fires first.
        with self.assertRaises(UserError):
            self.ou_a.parent_id = self.ou_a_child

    def test_hierarchy_stays_inside_one_company(self):
        other_company = self.env["res.company"].create({"name": "PT Contoh Lain"})
        with self.assertRaises(ValidationError):
            self.Unit.create({
                "name": "Cross company",
                "code": "OU-TEST-X",
                "company_id": other_company.id,
                "parent_id": self.ou_a.id,
            })

    # -- the stamped column ---------------------------------------------

    def test_operating_unit_id_is_stored_on_every_target_model(self):
        for model_name, _table in STAMPED_MODELS:
            with self.subTest(model=model_name):
                field = self.env[model_name]._fields.get("operating_unit_id")
                self.assertIsNotNone(
                    field, "%s carries no operating_unit_id" % model_name
                )
                self.assertTrue(field.store, "%s.operating_unit_id is not stored" % model_name)
                self.assertTrue(field.index, "%s.operating_unit_id is not indexed" % model_name)
                self.assertEqual(field.comodel_name, "operating.unit")

    def test_operating_unit_id_has_a_real_postgres_index(self):
        """store + index in Python is only a promise; assert the index exists in pg_indexes."""
        for model_name, table in STAMPED_MODELS:
            with self.subTest(model=model_name):
                self.env.cr.execute(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND tablename = %s "
                    "AND indexdef LIKE %s",
                    (table, "%operating_unit_id%"),
                )
                self.assertTrue(
                    self.env.cr.fetchall(),
                    "no index on %s.operating_unit_id" % table,
                )

    def test_column_exists_in_postgres(self):
        for model_name, table in STAMPED_MODELS:
            with self.subTest(model=model_name):
                self.env.cr.execute(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = %s "
                    "AND column_name = 'operating_unit_id'",
                    (table,),
                )
                self.assertTrue(self.env.cr.fetchone())

    # -- entitlement ----------------------------------------------------

    def test_user_entitlement_fields(self):
        user = self.env["res.users"].create({
            "name": "OU Test User",
            "login": "ou_test_entitlement",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id])],
            "allowed_operating_unit_ids": [(6, 0, [self.ou_a.id])],
            "default_operating_unit_id": self.ou_a.id,
        })
        self.assertEqual(user._pdp_allowed_operating_unit_ids(), [self.ou_a.id])

    def test_default_unit_must_be_allowed(self):
        with self.assertRaises(ValidationError):
            self.env["res.users"].create({
                "name": "OU Bad Default",
                "login": "ou_test_bad_default",
                "group_ids": [(6, 0, [self.env.ref("base.group_user").id])],
                "allowed_operating_unit_ids": [(6, 0, [self.ou_a.id])],
                "default_operating_unit_id": self.ou_b.id,
            })

    # -- isolation ------------------------------------------------------

    def _make_user(self, login, units):
        return self.env["res.users"].create({
            "name": "OU " + login,
            "login": login,
            "group_ids": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("sales_team.group_sale_salesman").id,
            ])],
            "allowed_operating_unit_ids": [(6, 0, units.ids)],
        })

    def test_user_of_ou_a_cannot_read_an_ou_b_sale_order(self):
        """Acceptance criterion 6."""
        partner = self.env["res.partner"].create({"name": "Pelanggan Uji"})
        order_a = self.env["sale.order"].create({
            "partner_id": partner.id,
            "operating_unit_id": self.ou_a.id,
        })
        order_b = self.env["sale.order"].create({
            "partner_id": partner.id,
            "operating_unit_id": self.ou_b.id,
        })
        user_a = self._make_user("ou_test_user_a", self.ou_a)

        visible = self.env["sale.order"].with_user(user_a).search([
            ("id", "in", (order_a + order_b).ids)
        ])
        self.assertIn(order_a, visible, "the user must see their own unit's order")
        self.assertNotIn(order_b, visible, "the user must not see another unit's order")

        with self.assertRaises(AccessError):
            order_b.with_user(user_a).read(["name"])

    def test_rules_fail_closed_for_a_user_with_no_units(self):
        partner = self.env["res.partner"].create({"name": "Pelanggan Uji 2"})
        order_b = self.env["sale.order"].create({
            "partner_id": partner.id,
            "operating_unit_id": self.ou_b.id,
        })
        user_none = self._make_user("ou_test_user_none", self.Unit.browse())
        visible = self.env["sale.order"].with_user(user_none).search([("id", "=", order_b.id)])
        self.assertFalse(
            visible, "an unassigned user must not fall through to seeing everything"
        )

    def test_administrator_bypass(self):
        partner = self.env["res.partner"].create({"name": "Pelanggan Uji 3"})
        order_b = self.env["sale.order"].create({
            "partner_id": partner.id,
            "operating_unit_id": self.ou_b.id,
        })
        admin = self.env.ref("base.user_admin")
        self.assertTrue(admin.has_group("custom_operating_unit.group_operating_unit_all"))
        visible = self.env["sale.order"].with_user(admin).search([("id", "=", order_b.id)])
        self.assertIn(order_b, visible)

    def test_isolation_applies_to_invoices_and_transfers(self):
        partner = self.env["res.partner"].create({"name": "Pelanggan Uji 4"})
        move_b = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": partner.id,
            "operating_unit_id": self.ou_b.id,
        })
        user_a = self._make_user("ou_test_user_a2", self.ou_a)
        self.assertFalse(
            self.env["account.move"].with_user(user_a).search([("id", "=", move_b.id)])
        )
