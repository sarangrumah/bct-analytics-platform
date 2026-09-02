# Part of custom_pdp_core. Licence: LGPL-3.
"""Tests for the PDP classification registry (frozen contract 01)."""

import psycopg2

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.custom_pdp_core.models.pdp_field_classification import PDP_CLASS_KEYS

#: Every model the warehouse reads, per the Platform-Addons brief, Scope 1.
REQUIRED_MODELS = [
    "res.partner",
    "res.users",
    "res.company",
    "product.template",
    "product.product",
    "sale.order",
    "sale.order.line",
    "account.account",
    "account.move",
    "account.move.line",
    "stock.move",
    "pos.order",
    "pos.order.line",
    "ppob.transaction",
]

#: Spot checks. These are the classifications the rest of the platform is entitled to assume.
SPOT_CHECKS = [
    ("res.partner", "name", "personal"),
    ("res.partner", "email", "personal"),
    ("res.partner", "phone", "personal"),
    ("res.partner", "street", "personal"),
    ("res.partner", "city", "personal"),
    ("res.partner", "vat", "sensitive"),
    ("res.partner", "barcode", "sensitive"),
    ("res.partner", "comment", "sensitive"),
    ("res.users", "password", "secret"),
    ("res.users", "totp_secret", "secret"),
    ("res.company", "name", "public"),
    ("product.template", "name", "public"),
    ("sale.order", "amount_total", "internal"),
    # The column fct_account_move_line is built on. `internal` on purpose: no transform,
    # so it lands readable. If this ever moves to a hashing class the mart breaks silently.
    ("account.account", "account_type", "internal"),
    ("stock.move", "product_qty", "internal"),
    ("ppob.transaction", "customer_ref", "sensitive"),
]


@tagged("post_install", "-at_install", "pdp")
class TestPdpFieldClassification(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Classification = cls.env["pdp.field.classification"]

    # -- taxonomy -------------------------------------------------------

    def test_taxonomy_is_exactly_five_frozen_classes(self):
        """Contract 01 freezes five classes. A sixth is a contract breach."""
        self.assertEqual(
            PDP_CLASS_KEYS,
            ("public", "internal", "personal", "sensitive", "secret"),
        )
        selection = dict(
            self.Classification._fields["pdp_class"]._description_selection(self.env)
        )
        self.assertEqual(set(selection), set(PDP_CLASS_KEYS))

    def test_seeded_classes_are_within_the_taxonomy(self):
        used = set(self.Classification.search([]).mapped("pdp_class"))
        self.assertTrue(used, "the registry must not be empty after install")
        self.assertFalse(used - set(PDP_CLASS_KEYS))

    # -- constraints ----------------------------------------------------

    @mute_logger("odoo.sql_db")
    def test_model_field_pair_is_unique(self):
        self.Classification.create({
            "model_name": "x.unit.test",
            "field_name": "some_column",
            "pdp_class": "internal",
        })
        with self.assertRaises(psycopg2.errors.UniqueViolation):
            with self.env.cr.savepoint():
                self.Classification.create({
                    "model_name": "x.unit.test",
                    "field_name": "some_column",
                    "pdp_class": "personal",
                })

    @mute_logger("odoo.sql_db")
    def test_drop_to_null_requires_sensitive(self):
        """drop_to_null is the 'sensitive' NULL-drop of contract 01, nothing else."""
        with self.assertRaises(psycopg2.errors.CheckViolation):
            with self.env.cr.savepoint():
                self.Classification.create({
                    "model_name": "x.unit.test",
                    "field_name": "another_column",
                    "pdp_class": "personal",
                    "drop_to_null": True,
                })

    def test_blank_names_rejected(self):
        with self.assertRaises(ValidationError):
            self.Classification.create({
                "model_name": "   ",
                "field_name": "col",
                "pdp_class": "internal",
            })

    # -- seeded map -----------------------------------------------------

    def test_map_covers_every_required_model(self):
        payload = self.Classification.get_classification_map()
        self.assertEqual(payload["contract"], "01-classification")
        self.assertEqual(payload["classes"], list(PDP_CLASS_KEYS))
        missing = [m for m in REQUIRED_MODELS if not payload["models"].get(m)]
        self.assertFalse(
            missing, "models absent from the seeded classification map: %s" % missing
        )

    def test_spot_checks(self):
        for model_name, field_name, expected in SPOT_CHECKS:
            with self.subTest(model=model_name, field=field_name):
                row = self.Classification.get_classification(model_name, field_name)
                self.assertTrue(
                    row, "%s.%s carries no classification" % (model_name, field_name)
                )
                self.assertEqual(row["pdp_class"], expected)

    def test_unclassified_field_returns_nothing(self):
        """The loader must be able to hard-fail. An unknown column returns False, not a class."""
        self.assertFalse(
            self.Classification.get_classification("res.partner", "no_such_column_xyz")
        )
        self.assertEqual(
            self.Classification.get_unclassified_fields(
                "res.partner", ["email", "no_such_column_xyz"]
            ),
            ["no_such_column_xyz"],
        )

    def test_map_restricted_to_requested_models(self):
        payload = self.Classification.get_classification_map(["res.partner"])
        self.assertEqual(list(payload["models"]), ["res.partner"])

    def test_secret_columns_are_not_extractable(self):
        """A `secret` column is never named in the loader's SELECT list."""
        extractable = self.Classification.get_extractable_fields("res.users")
        self.assertIn("login", extractable)
        self.assertNotIn("password", extractable)
        self.assertNotIn("totp_secret", extractable)

    # -- coverage -------------------------------------------------------

    def test_no_installed_column_is_unclassified(self):
        """Every physical column of every warehouse-read model must carry a class.

        Models that are not installed in this database are skipped: the registry deliberately
        depends on `base` only and classifies models by name.
        """
        gaps = self.Classification.check_coverage(REQUIRED_MODELS)
        self.assertFalse(
            gaps,
            "unclassified columns found - the CDC loader would refuse to start:\n%s"
            % "\n".join("  %s: %s" % (m, ", ".join(c)) for m, c in sorted(gaps.items())),
        )

    def test_company_dependent_columns_are_never_hashed(self):
        """A company_dependent field is a per-company jsonb map, not a scalar.

        Hashing ``{"1": "BC123", "2": "BC456"}`` yields a digest of a composite: it identifies
        nobody, joins to nothing, and still discloses how many companies hold a value for that
        person. Any such column that is personal-or-sensitive must therefore carry
        ``drop_to_null``. Asserted against the live database so that a future Odoo release which
        makes another column company_dependent fails the build instead of silently shipping a
        meaningless digest.
        """
        self.env.cr.execute(
            """
            SELECT f.model, f.name, c.pdp_class, c.drop_to_null
              FROM ir_model_fields f
              JOIN pdp_field_classification c
                ON c.model_name = f.model AND c.field_name = f.name
             WHERE f.company_dependent IS TRUE
               AND f.store IS TRUE
               AND c.pdp_class IN ('personal', 'sensitive')
               AND c.drop_to_null IS NOT TRUE
             ORDER BY f.model, f.name
            """
        )
        offenders = self.env.cr.fetchall()
        self.assertFalse(
            offenders,
            "company_dependent columns classified for hashing rather than NULL-drop: %s"
            % ", ".join("%s.%s (%s)" % (m, f, c) for m, f, c, _d in offenders),
        )

    def test_no_non_text_column_is_classified_for_hashing(self):
        """The CDC loader's startup validation, mirrored on the producer side.

        Contract 01: a column whose transform resolves to ``hmac_sha256`` and whose physical type
        is not text makes the loader refuse to start. That check lives in the consumer, where it
        fires hours later in someone else's terminal. Asserting the same invariant here makes the
        registry itself incapable of shipping the ``res.partner.barcode`` defect.

        Broader than ``test_company_dependent_columns_are_never_hashed`` on purpose: that test
        catches a company-keyed jsonb map, this one catches EVERY non-text type, including the
        language-keyed jsonb map a ``translate=True`` field is stored in (``account.account.name``,
        ``account.account.description``) and any numeric column somebody classifies personal.

        Note the second assertion. A query whose passing state is an empty result is
        indistinguishable from a query that examined nothing (PLAN.md, instance 12), so the size of
        the population actually inspected is asserted too, and printed on failure.
        """
        # udt_name spelling, matching bct_cdc.policy.TEXT_TYPES / warehouse_ctl.TEXTUAL_TYPES.
        text_udts = ("text", "varchar", "bpchar", "char", "name", "citext")
        self.env.cr.execute(
            """
            SELECT c.model_name, c.field_name, c.pdp_class, t.udt_name
              FROM pdp_field_classification c
              JOIN ir_model m ON m.model = c.model_name
              JOIN information_schema.columns t
                ON t.table_schema = 'public'
               AND t.table_name = replace(c.model_name, '.', '_')
               AND t.column_name = c.field_name
             WHERE c.active
               AND (c.pdp_class = 'personal'
                    OR (c.pdp_class = 'sensitive' AND c.drop_to_null IS NOT TRUE))
             ORDER BY c.model_name, c.field_name
            """
        )
        hashed = self.env.cr.fetchall()
        offenders = [row for row in hashed if row[3] not in text_udts]
        self.assertTrue(
            hashed,
            "the population inspected was EMPTY - no classified column resolved to a hash and "
            "matched a physical column, so this test proved nothing. Check the model_name -> "
            "table_name derivation before believing a green result.",
        )
        self.assertFalse(
            offenders,
            "columns classified for hashing whose physical type is not text (%d inspected):\n%s"
            % (
                len(hashed),
                "\n".join("  %s.%s: %s over %s" % r for r in offenders),
            ),
        )

    # -- access ---------------------------------------------------------

    def test_plain_user_may_read_but_not_write(self):
        user = self.env["res.users"].create({
            "name": "PDP Test Reader",
            "login": "pdp_test_reader",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        registry = self.Classification.with_user(user)
        self.assertTrue(registry.search_count([]) > 0)
        # AccessError specifically, not a bare Exception: a blind assertRaises would also pass on a
        # TypeError from a malformed vals dict, i.e. it would prove nothing about the ACL.
        with self.assertRaises(AccessError):
            registry.create({
                "model_name": "x.denied",
                "field_name": "col",
                "pdp_class": "internal",
            })
